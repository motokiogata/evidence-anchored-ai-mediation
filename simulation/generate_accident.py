# -*- coding: utf-8 -*-
"""
CARLA で交通事故シナリオを生成し、衝突する2車のドラレコ視点を同時録画する。

使い方（CARLAサーバ起動済み前提）:
  python generate_accident.py --scenario A_intersection --out ../videos
  python generate_accident.py --scenario B_right_turn   --out ../videos
  python generate_accident.py --list-junctions --map Town10HD_Opt   # 交差点探索デバッグ

出力: <out>/<scenario>/<viewpoint>/*.png（フレーム）。ffmpeg での結合は run_all.sh が行う。

方式: 同期モード＋物理OFFのキネマティック駆動。実車線ウェイポイントに沿った経路を
事前計算し、arc-length で位置を刻む。両車が同時刻に交差点中央へ到達→衝突。
"""
import argparse
import math
import os
import queue
import shutil

import carla


# ---------------------------------------------------------------------------
# 幾何ユーティリティ
# ---------------------------------------------------------------------------
def yaw_of(wp):
    return wp.transform.rotation.yaw


def ang_diff(a, b):
    """角度差を -180..180 に正規化。"""
    d = (a - b + 180.0) % 360.0 - 180.0
    return d


def loc_dist(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


def build_lane_path(entry_wp, back_m=120.0, fwd_m=40.0, step=0.6, exit_yaw=None):
    """entry_wp の車線に沿って、手前 back_m 〜 先 fwd_m のウェイポイント列を作る。

    exit_yaw を与えると、交差点内では「その出口方位へ向かう分岐」を選ぶ（右左折の追従）。
    与えない場合は直進を優先（交差点で暴れないように）。
    戻り値: [(carla.Waypoint, cum_arc_length), ...]
    """
    # 手前へさかのぼって開始点を得る（接近路は直進）。
    # 交差点付近で previous() は分岐（直交レーン等）を返すため、盲目的に prevs[0] を
    # 掴むと接近路が直交路へ逸れ、パスがS字に暴れる。前方walkと同様に「現在のyawに
    # 最も近い分岐」を選び、接近路を確実に直進させる。
    start = entry_wp
    walked = 0.0
    while walked < back_m:
        prevs = start.previous(step)
        if not prevs:
            break
        cur_yaw = yaw_of(start)
        start = min(prevs, key=lambda w: abs(ang_diff(yaw_of(w), cur_yaw)))
        walked += step
    # 前方へ進みながら収集
    path = []
    cur = start
    total = 0.0
    prev_loc = cur.transform.location
    limit = back_m + fwd_m
    guard = 0
    while total < limit and guard < 4000:
        guard += 1
        loc = cur.transform.location
        seg = loc_dist(loc, prev_loc)
        total += seg
        path.append((cur, total))
        prev_loc = loc
        nxts = cur.next(step)
        if not nxts:
            break
        cur_yaw = yaw_of(cur)
        if exit_yaw is not None and cur.is_junction:
            # 交差点内は出口方位へ向かう分岐を選ぶ（曲がりに追従）
            cur = min(nxts, key=lambda w: abs(ang_diff(yaw_of(w), exit_yaw)))
        else:
            # それ以外は直進を優先
            cur = min(nxts, key=lambda w: abs(ang_diff(yaw_of(w), cur_yaw)))
    return path


def path_point_at(path, s):
    """arc-length s の位置と向き(yaw)を線形補間で返す -> (carla.Location, yaw)。"""
    if s <= path[0][1]:
        wp = path[0][0]
        return wp.transform.location, yaw_of(wp)
    for i in range(1, len(path)):
        if path[i][1] >= s:
            w0, s0 = path[i - 1]
            w1, s1 = path[i]
            t = (s - s0) / max(s1 - s0, 1e-6)
            l0, l1 = w0.transform.location, w1.transform.location
            loc = carla.Location(x=l0.x + (l1.x - l0.x) * t,
                                 y=l0.y + (l1.y - l0.y) * t,
                                 z=l0.z + (l1.z - l0.z) * t + 0.1)
            yaw = yaw_of(w0) + ang_diff(yaw_of(w1), yaw_of(w0)) * t
            return loc, yaw
    wp = path[-1][0]
    return wp.transform.location, yaw_of(wp)


def nearest_arc_to(path, center):
    """path 上で center に最も近い点の arc-length を返す（＝衝突点）。"""
    best_s, best_d = path[0][1], 1e9
    for wp, s in path:
        d = loc_dist(wp.transform.location, center)
        if d < best_d:
            best_d, best_s = d, s
    return best_s


def path_max_deviation(path, s_lo, s_hi):
    """[s_lo,s_hi] 区間で、区間中央のyawからの最大偏差(度)と純変化(度)を返す。
    接近路がまっすぐか（＝車が交差点をカーブせず通過するか）の判定に使う。
    """
    seg = [(wp, s) for wp, s in path if s_lo - 3.0 <= s <= s_hi + 3.0]
    if len(seg) < 3:
        return 999.0, 0.0
    mid_yaw = yaw_of(seg[len(seg) // 2][0])
    max_dev = max(abs(ang_diff(yaw_of(wp), mid_yaw)) for wp, _ in seg)
    net = ang_diff(yaw_of(seg[-1][0]), yaw_of(seg[0][0]))
    return max_dev, net


def mutual_closest_arcs(path_a, path_b):
    """2経路が最も接近する点の arc-length ペア (s_a, s_b) を返す（＝実際の衝突点）。

    交差点中央からの最近接では両車の停止点がズレて衝突しないため、
    2経路そのものの相互最近接点を衝突点に採用する。
    """
    best = (path_a[0][1], path_b[0][1], 1e9)
    for wa, sa in path_a:
        la = wa.transform.location
        for wb, sb in path_b:
            d = loc_dist(la, wb.transform.location)
            if d < best[2]:
                best = (sa, sb, d)
    return best[0], best[1], best[2]


# ---------------------------------------------------------------------------
# 交差点選択
# ---------------------------------------------------------------------------
def all_junctions(world):
    """マップ上のユニークな交差点を (id, junction, center_location) で返す。"""
    topo = world.get_map().get_topology()
    seen = {}
    for a, b in topo:
        for wp in (a, b):
            if wp.is_junction:
                j = wp.get_junction()
                if j and j.id not in seen:
                    bb = j.bounding_box
                    seen[j.id] = (j, bb.location)
    return [(jid, j, loc) for jid, (j, loc) in seen.items()]


def junction_entries(junction):
    """交差点の各流入ウェイポイント(entry)と、その通過後(exit)の向きを返す。

    戻り値: [(entry_wp, exit_wp, entry_yaw, turn_deg), ...]  turn_deg>0 は右折側。
    """
    res = []
    for entry, ex in junction.get_waypoints(carla.LaneType.Driving):
        ey, xy = yaw_of(entry), yaw_of(ex)
        res.append((entry, ex, ey, ang_diff(xy, ey)))
    return res


def junction_is_signalized(world, center, radius=20.0):
    """交差点中心の近傍に信号機があるか（信号あり/なし交差点の判定）。"""
    for tl in world.get_actors().filter("traffic.traffic_light*"):
        if loc_dist(tl.get_transform().location, center) < radius:
            return True
    return False


def pick_junction_and_lanes(world, scenario, require_unsignalized=False, target_xy=None):
    """シナリオに応じて交差点と victim/opponent の流入車線を選ぶ。

    A_intersection: victim 直進 + opponent 直進で進入方向が ~90° 異なる（出会い頭）。
    B_right_turn : victim 直進 + opponent は対向(~180°)から右折してくる。
    require_unsignalized=True で「信号機が実在しない交差点のみ」を選ぶ
    （無信号マップでの撮り直し用。Town10HDでは候補ゼロになるため既定はFalse）。
    target_xy=(x,y) を与えると、その座標近傍(15m以内)の交差点だけを対象にする
    （巨大交差点を掴んでクラッシュ/幾何破綻するのを避け、見た目の良い十字路を狙い撮り）。
    戻り値: (victim_entry_wp, opponent_entry_wp, center_location)
    """
    want_turn = scenario.get("kind") == "direct_vs_turn"
    # 信号ありシナリオ(C)は信号ありの交差点を要求。
    # 信号なしシナリオは幾何優先で選び、レンダ時に信号をOff(消灯)にして表現する
    # （Town10の直交4差路は全て信号ありのため、消灯で"信号のない交差点"を作る）。
    want_sig = scenario.get("junction_type", "any")
    candidates = []
    for jid, j, center in all_junctions(world):
        if want_sig == "signalized" and not junction_is_signalized(world, center):
            continue
        # 無信号マップでの撮り直し: 信号機が実在しない交差点のみを選ぶ（--require-unsignalized）。
        # Town10HD等の信号だらけのマップでは候補ゼロになるため、既定は無効。
        if require_unsignalized and junction_is_signalized(world, center):
            continue
        # 座標指定: その交差点だけに絞る（巨大交差点回避・狙い撮り）。
        if target_xy is not None and math.hypot(center.x - target_xy[0],
                                                center.y - target_xy[1]) > 15.0:
            continue
        entries = junction_entries(j)
        straights = [e for e in entries if abs(e[3]) < 20.0]   # 直進レーン
        # CARLA標準マップは右側通行。日本の「対向右折が直進車の進路を横切る」構造は
        # 右側通行では「対向"左折"」に相当する（turn_deg が負=左折）。
        crossing_turns = [e for e in entries if -110.0 < e[3] < -40.0]
        if len(straights) < 2:
            continue
        for v in straights:
            if want_turn:
                # opponent は対向(~180°)から曲がって victim の直進路を横切る
                for o in crossing_turns:
                    if abs(abs(ang_diff(o[2], v[2])) - 180.0) < 40.0:
                        candidates.append((len(entries), v, o, center))
            else:
                # opponent は直進で victim と ~90°違い（出会い頭）
                for o in straights:
                    if abs(abs(ang_diff(o[2], v[2])) - 90.0) < 30.0:
                        candidates.append((len(entries), v, o, center))
    if not candidates:
        raise RuntimeError(f"適切な交差点が見つかりません: {scenario['id']}")
    # 車線ペアを「衝突する(gap小)」かつ「victimの接近路がまっすぐ(交差点で曲がらない)」で選ぶ。
    # 旧実装は gap 最小だけで選び、カーブ上のレーン(接近路が湾曲)を掴んで victim が
    # 曲がって見える不具合があった。接近路の直進性をスコアに入れて防ぐ。
    v_speed = scenario["vehicles"]["victim"]["speed_mps"]
    o_speed = scenario["vehicles"]["opponent"]["speed_mps"]
    t_col = scenario["sim"]["collision_at_s"]
    candidates.sort(key=lambda c: -c[0])   # 大きい交差点を優先的に評価
    best = None   # (score, gap, v_dev, o_net, v, o, center)
    for _, v, o, center in candidates[:60]:
        o_exit_yaw = yaw_of(o[1]) if want_turn else None
        pv = build_lane_path(v[0])
        po = build_lane_path(o[0], exit_yaw=o_exit_yaw)
        s_v, s_o, gap = mutual_closest_arcs(pv, po)
        if gap > 2.5:            # そもそも衝突しないペアは除外
            continue
        v_dev, _ = path_max_deviation(pv, s_v - v_speed * t_col, s_v)   # victim接近の湾曲
        o_dev, o_net = path_max_deviation(po, s_o - o_speed * t_col, s_o)
        # スコア: gap小 + victim接近まっすぐ を最優先。
        score = gap + v_dev * 0.15
        if want_turn:
            score -= min(abs(o_net), 90.0) * 0.02   # 相手が実際に曲がるほど良い(B)
        else:
            score += o_dev * 0.05                    # 相手も直進が望ましい(A/出会い頭)
        if best is None or score < best[0]:
            best = (score, gap, v_dev, o_net, v, o, center)
    if best is None:
        raise RuntimeError(f"衝突する車線ペアが見つかりません: {scenario['id']}")
    score, gap, v_dev, o_net, v, o, center = best
    print(f"[info] selected lane pair gap={gap:.2f}m victim_dev={v_dev:.1f}° "
          f"opp_net={o_net:+.0f}° score={score:.2f}")
    # v/o は (entry, exit, entry_yaw, turn_deg)。exit も返す（曲がり誘導用）。
    return v[0], o[0], (o[1] if want_turn else None), center


# ---------------------------------------------------------------------------
# 録画
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 修正要素（判例タイムズの加減算に対応する、映像に埋め込む"争点"）
# ---------------------------------------------------------------------------
# 修正要素の定義（法的効果・非対称）は backend/judicial_map.py が単一の真実源。
# --mods には judicial_map のキー（opp_stop_violation 等）を渡す。
# 本ファイルは effect(speeding/run_red/stop_violation/early_turn) の描画のみ担う。
# 生フレームの出力先サブディレクトリ。完成mp4(反転後)と同じ階層に置くと
# 「videos/ の中に反転前と反転後が混在」して取り違えるため、名前で区別する。
# rhd = 右側通行のまま（CARLAの素のレンダ）。左側通行化は encode.sh が hflip で行う。
FRAMES_SUBDIR = "_frames_rhd"

SPEED_MULT = 1.7            # victim_speeding（自車の著しい速度超過）
STOP_VIOLATION_MULT = 1.6   # opp_stop_violation（一時停止無視＝減速せず突入する相手）
EARLY_TURN_MULT = 1.5       # opp_early_turn（早回り右折＝機敏に膨らむ相手）


def find_light_for_entry(world, entry_wp, max_d=30.0):
    """entry_wp を進む運転者に正対し、前方にある信号機を返す。

    信号はふつう対向（進行方向の逆）を向いて設置される。よって
    light_yaw ≈ entry_yaw+180 を満たし、かつ車両の前方にあるものを選ぶ。
    """
    import math as _m
    ewp = entry_wp.transform.location
    eyaw = yaw_of(entry_wp)
    hx, hy = _m.cos(_m.radians(eyaw)), _m.sin(_m.radians(eyaw))  # 進行方向
    best, best_score = None, 1e9
    for tl in world.get_actors().filter("traffic.traffic_light*"):
        t = tl.get_transform()
        dx, dy = t.location.x - ewp.x, t.location.y - ewp.y
        d = _m.hypot(dx, dy)
        if d > max_d:
            continue
        ahead = dx * hx + dy * hy            # 前方なら正
        facing = abs(ang_diff(t.rotation.yaw, eyaw + 180.0))  # 運転者に正対なら小
        if ahead > 0 and facing < 45.0:
            score = facing + d * 0.5         # 正対を優先、次に近さ
            if score < best_score:
                best, best_score = tl, score
    return best


def _S(name):
    return getattr(carla.TrafficLightState, name)


def lights_facing_entry(world, entry_wp, max_d=48.0, facing_tol=52.0):
    """entry を進むドライバーに正対する信号機を【全て】返す（近側/対岸の複数ヘッド）。
    最寄り1灯だけだと、見えている別ヘッドが巡回して別色を映すため、正対ヘッドは全部掴む。
    """
    ewp = entry_wp.transform.location
    eyaw = yaw_of(entry_wp)
    hx, hy = math.cos(math.radians(eyaw)), math.sin(math.radians(eyaw))
    out = []
    for tl in world.get_actors().filter("traffic.traffic_light*"):
        t = tl.get_transform()
        dx, dy = t.location.x - ewp.x, t.location.y - ewp.y
        d = math.hypot(dx, dy)
        if d > max_d:
            continue
        ahead = dx * hx + dy * hy
        facing = abs(ang_diff(t.rotation.yaw, eyaw + 180.0))   # ドライバーに正対なら小
        if ahead > -3.0 and facing < facing_tol:
            out.append(tl)
    return out


def apply_crossing_signals(world, entries, victim_state, opp_state):
    """信号交差点(C)で victim/opponent 双方の前方信号を決定論的に固定する。

    決定論シムでは制御し損ねたヘッドが巡回して同じ色を映し込むため、
    victim/opponent それぞれに正対する【全ヘッド】＋その所属グループを Green+freeze
    で野放し巡回を止めてから、victim 正対ヘッド=victim_state / opponent 正対ヘッド=opp_state
    に固定する。戻り値: 毎tick再適用するための [(tl, state), ...]。
    """
    vlights = lights_facing_entry(world, entries["victim"])
    olights = lights_facing_entry(world, entries["opponent"])
    if not vlights or not olights:
        print(f"[warn] 信号取得: victim={len(vlights)}灯 opponent={len(olights)}灯")
    eyaw = yaw_of(entries["victim"])

    # 交差点グループの全灯を集める（victim/opponent 正対ヘッド＋その所属グループ）。
    group = set(vlights) | set(olights)
    for tl in list(group):
        for g in tl.get_group_traffic_lights():
            group.add(g)

    # ★各灯を「軸」で分類して確定する（従来は下地Greenで“傍観灯”が緑のまま残り、
    #   victimの視界正面に緑が映って『自分黄/赤なのに前方が緑』の矛盾を生んでいた）。
    #   victim進行方向(eyaw)に平行な灯＝victimの軸＝victim_state、直交する灯＝交差軸＝opp_state。
    #   交差点は相補位相なので、これで傍観灯も含め全灯が物理整合した色になる（緑の映り込み解消）。
    vst, ost = _S(victim_state), _S(opp_state)
    targets = []
    n_v = n_o = 0
    _dbg = os.environ.get("DBG_SIG") == "1"
    _ve = entries["victim"].transform.location
    _rows = []
    for tl in group:
        tyaw = tl.get_transform().rotation.yaw
        axis = abs(ang_diff(tyaw, eyaw)) % 180.0
        on_victim_axis = (axis < 45.0 or axis > 135.0)
        st = vst if on_victim_axis else ost
        tl.set_state(st); tl.freeze(True); targets.append((tl, st))
        n_v += on_victim_axis; n_o += (not on_victim_axis)
        if _dbg:
            tl_loc = tl.get_transform().location
            dx, dy = tl_loc.x - _ve.x, tl_loc.y - _ve.y
            bearing = ang_diff(math.degrees(math.atan2(dy, dx)), eyaw)  # victim進行基準の方位（0=正面）
            _rows.append((abs(bearing), bearing, math.hypot(dx, dy), tyaw, axis,
                          "victim" if on_victim_axis else "cross",
                          "V:" + victim_state if on_victim_axis else "O:" + opp_state,
                          tl.id))
    if _dbg:
        print(f"[DBG_SIG] eyaw(victim heading)={eyaw:.0f}  victim_state={victim_state} opp_state={opp_state}")
        for ab, bearing, dist, tyaw, axis, cls, stt, tid in sorted(_rows):
            print(f"[DBG_SIG] id={tid} bearing={bearing:+.0f} dist={dist:.0f} yaw={tyaw:.0f} "
                  f"axis={axis:.0f} -> {cls} state={stt}")
    # 正対ヘッドは分類の取りこぼし保険として明示的に上書き確定。
    for tl in vlights:
        tl.set_state(vst); tl.freeze(True)
    for tl in olights:
        tl.set_state(ost); tl.freeze(True)
    print(f"[info] 信号固定(軸分類): victim軸({victim_state})×{n_v}灯 / 交差軸({opp_state})×{n_o}灯 "
          f"(正対 victim={len(vlights)} opp={len(olights)}, group計{len(group)})")
    return targets


def _project_to_image(cam_tf, loc, w, h, fov):
    """世界座標 loc をカメラ画像の(u,v,depth)へ投影。depth<=0は背後。SIGDEBUG検証用。"""
    import numpy as _np
    m = _np.array(cam_tf.get_inverse_matrix())
    pw = _np.array([loc.x, loc.y, loc.z, 1.0])
    pc = m @ pw                                   # UE: x=前, y=右, z=上
    depth = pc[0]
    if depth <= 0.01:
        return None
    pt = _np.array([pc[1], -pc[2], pc[0]])        # 標準カメラ系(x右,y下,z前)
    f = w / (2.0 * math.tan(math.radians(fov) / 2.0))
    u = f * pt[0] / pt[2] + w / 2.0
    v = f * pt[1] / pt[2] + h / 2.0
    return (u, v, depth)


def setup_modifiers(scenario_id, world, mods, entries, speeds, actors, signalized=False,
                    fault_cell="mixed"):
    """judicial_map の修正要素キー(mods)を映像効果として適用する。

    speeds(dict) を破壊的に更新し、(HUD {role:kmh}, signal_targets, signal_hud) を返す。
    映像効果: speeding / run_red / stop_violation / early_turn。
    信号(run_red/yellow_entry)は個別適用せず、victim/opponent 両方の状態を集約して
    ループ後に apply_crossing_signals で決定論的に一括固定する（片側巡回の映り込み防止）。
    """
    from judicial_map import video_mods
    applied = []
    signal_targets = []
    signal_hud = {}      # role -> HUD文字列（自車前方信号の状態。黄進入HUD等）
    victim_yellow = False
    victim_red = False
    opp_red = False
    # fault_cell は cell_overrides（C③＝赤信号進入 red_entry）を解決するために必要。
    for vm in video_mods(scenario_id, mods, fault_cell):
        role, effect, key = vm["party"], vm["effect"], vm["key"]
        if effect == "speeding":
            speeds[role] *= SPEED_MULT
            applied.append(f"{key}:速度超過(→{speeds[role]*3.6:.0f}km/h)")
        elif effect == "run_red":
            # 相手の赤信号無視。実際の信号固定はループ後に一括で行う。
            opp_red = True
            applied.append(f"{key}:信号無視(相手前方=赤)")
        elif effect == "yellow_entry":
            # 自車の黄信号見切り進入。信号固定はループ後。HUD表示は下でまとめて設定。
            victim_yellow = True
            applied.append(f"{key}:黄信号見切り進入(自車前方=黄)")
        elif effect == "red_entry":
            # 自車の赤信号進入（信号残り）＝C③のみ。相手は青なので位相は下の else 分岐と一致する。
            # judicial_map の cell_overrides から来る効果キー（真値も +40 に差し替わっている）。
            victim_red = True
            applied.append(f"{key}:赤信号進入(自車前方=赤)")
        elif effect == "stop_violation":
            # CARLA標準に停止標識メッシュが無いため、標識は出さず【挙動】で違反を可視化する。
            # 一時停止規制を無視＝減速せず高速で交差点へ突入。相手速度を増速して
            # 「止まる素振りなくバッと入ってくる」動きにする（衝突時刻は s0 逆算で維持）。
            speeds[role] *= STOP_VIOLATION_MULT
            applied.append(f"{key}:不停止で突入(→{speeds[role]*3.6:.0f}km/h)")
        elif effect == "early_turn":
            # 早回り右折＝交差点をショートカットするように早め＆機敏に曲がる。
            # 相手の旋回速度を上げて「急に膨らんで入ってくる」動きで可視化する。
            speeds[role] *= EARLY_TURN_MULT
            applied.append(f"{key}:早回り右折(→{speeds[role]*3.6:.0f}km/h)")
        else:
            print(f"[warn] 未対応の映像効果: {effect} ({key})")
    # 信号交差点(C): victim/opponent の前方信号を【交差点の物理拘束(相補)】で導出する。
    #   交差する2方向は必ず相補＝一方が青/黄なら他方は必ず赤。独立フラグで別々に決めると
    #   「自分=黄・相手=青」等の物理的にありえない組合せが生じるため、位相で一括決定する。
    #     ・opp_red(相手が赤無視)  → 相手=赤。自分は 黄進入ONなら黄／OFFなら青(＝適法)。
    #                               ①潔白: 自分青/相手赤  ②混合: 自分黄/相手赤
    #     ・opp_red なし(相手は適法に青) → 相手=青。この位相では自分は必ず赤。
    #                               ③主責: 自分赤(赤残り進入)/相手青
    if signalized:
        if opp_red:
            if victim_red:   # 相手も赤・自分も赤は交差点の相補位相としてありえない
                raise SystemExit("[fatal] opp_run_red と red_entry は同時に成立しない（位相矛盾）")
            victim_state = "Yellow" if victim_yellow else "Green"
            opp_state = "Red"
        else:
            # 相手が青(適法)＝交差方向は赤。自分は赤残りで見切り進入した(＝主責の核)。
            victim_state = "Red"
            opp_state = "Green"
        # ★被害者/相手の信号が「逆転して見える」不具合の修正（2026-08-04）。
        #   この交差点の幾何では、被害者ダッシュカムに前方で映る点灯灯は、軸分類上は
        #   相手側（交差軸）に割り当たる灯だった＝被害者が相手の信号色を読んでしまう。
        #   in-world への割当を入れ替え、被害者が読む前方灯に victim_state を映す。
        #   相補位相なので相手側の読む灯には opp_state が映る（両視点とも正しくなる）。
        signal_targets = apply_crossing_signals(
            world, entries, victim_state=opp_state, opp_state=victim_state)
        # HUDは真値の victim_state を明示（①潔白=緑 / ②混合=黄 / ③主責=赤）。in-world と一致。
        signal_hud["victim"] = victim_state.upper()   # GREEN / YELLOW / RED
    if applied:
        print("[info] 修正要素:", ", ".join(applied))
    hud = {role: round(speeds[role] * 3.6) for role in speeds}
    return hud, signal_targets, signal_hud


def attach_dashcam(world, vehicle, w=1280, h=720, fov=95):
    # fov は水平画角(度)。広角ほど交差方向の車が早く・長く映る（サイドウィンドウ相当の視界）。
    # 95=従来の狭角ドラレコ / 130前後=広角ドラレコ（study3の被害者動画は広角で撮る）。
    bp = world.get_blueprint_library().find("sensor.camera.rgb")
    bp.set_attribute("image_size_x", str(w))
    bp.set_attribute("image_size_y", str(h))
    bp.set_attribute("fov", str(fov))
    # マニュアル露出で固定（histogram自動露出は明るい方向で白飛びするため）。
    # shutter_speed↑=暗く, iso↓=暗く, fstop↑=暗く。屋外昼向けに白飛びしない値へ。
    for attr, val in [("exposure_mode", "manual"),
                      ("shutter_speed", "120.0"),
                      ("iso", "100.0"),
                      ("fstop", "4.5"),
                      ("gamma", "2.2")]:
        if bp.has_attribute(attr):
            bp.set_attribute(attr, val)
    # 運転席前・フロントガラス付近
    tf = carla.Transform(carla.Location(x=0.5, y=-0.3, z=1.25),
                         carla.Rotation(pitch=-5.0))
    cam = world.spawn_actor(bp, tf, attach_to=vehicle)
    q = queue.Queue()
    cam.listen(q.put)
    return cam, q


def spawn_vehicle(world, spec, loc, yaw):
    bp = world.get_blueprint_library().find(spec["blueprint"])
    if bp.has_attribute("color") and spec.get("color"):
        bp.set_attribute("color", spec["color"])
    tf = carla.Transform(carla.Location(x=loc.x, y=loc.y, z=loc.z + 0.3),
                         carla.Rotation(yaw=yaw))
    v = world.try_spawn_actor(bp, tf)
    if v is None:
        raise RuntimeError("車両スポーン失敗: " + spec["blueprint"])
    v.set_simulate_physics(False)   # キネマティック駆動
    return v


def run(scenario, out_dir, host, port, mods=(), require_unsignalized=False, target_xy=None, fov=95,
        fault_cell="mixed"):
    client = carla.Client(host, port)
    client.set_timeout(30.0)
    world = client.get_world()
    # 注意: このGPU構成では load_world がVulkanクラッシュを招くため、
    # マップ不一致時のみ再読込（通常はパッケージ既定のTown10HD_Optをそのまま使う）。
    if not world.get_map().name.endswith(scenario["map"]):
        world = client.load_world(scenario["map"])

    # 天候: やや曇天で動的レンジを圧縮し白飛びを防ぐ（均一な光）
    world.set_weather(carla.WeatherParameters(
        cloudiness=55.0, precipitation=0.0, sun_altitude_angle=60.0,
        sun_azimuth_angle=20.0, fog_density=0.0, wetness=0.0))

    # シナリオ設定から狙い撮り交差点・無信号要求を既定値として取り込む
    # （run_all.sh はフラグ無しで呼ぶため、設定ファイル側で確定させる）。
    if target_xy is None:
        target_xy = scenario.get("junction_xy")
    if scenario.get("junction_type") == "unsignalized":
        require_unsignalized = True

    settings = world.get_settings()
    orig = (settings.synchronous_mode, settings.fixed_delta_seconds)
    fps = scenario["sim"]["fps"]
    settings.synchronous_mode = True
    settings.fixed_delta_seconds = 1.0 / fps
    world.apply_settings(settings)

    actors = []
    try:
        v_entry, o_entry, o_exit, center = pick_junction_and_lanes(
            world, scenario, require_unsignalized=require_unsignalized, target_xy=target_xy)
        print(f"[info] junction center=({center.x:.1f},{center.y:.1f}) "
              f"victim_yaw={yaw_of(v_entry):.0f} opp_yaw={yaw_of(o_entry):.0f}"
              + (f" opp_exit_yaw={yaw_of(o_exit):.0f}" if o_exit else ""))

        # 信号なしシナリオ: 近傍の信号を消灯(Off)して"信号のない交差点"として見せる
        if scenario.get("junction_type") == "unsignalized":
            n = 0
            for tl in world.get_actors().filter("traffic.traffic_light*"):
                if loc_dist(tl.get_transform().location, center) < 40.0:
                    tl.set_state(carla.TrafficLightState.Off); tl.freeze(True); n += 1
            if n:
                print(f"[info] 信号なし表現: 近傍{n}基の信号を消灯")

        paths, vehicles, cams = {}, {}, {}
        # opponent は曲がる場合、交差点内で exit 方位へ誘導する
        o_exit_yaw = yaw_of(o_exit) if o_exit is not None else None
        raw_paths = {
            "victim": build_lane_path(v_entry),
            "opponent": build_lane_path(o_entry, exit_yaw=o_exit_yaw),
        }
        # 2経路の相互最近接点を実際の衝突点とする（確実に衝突させる）
        s_v, s_o, dmin = mutual_closest_arcs(raw_paths["victim"], raw_paths["opponent"])
        print(f"[info] impact: victim_s={s_v:.1f} opp_s={s_o:.1f} gap={dmin:.2f}m")
        imp = {"victim": s_v, "opponent": s_o}
        for role in scenario["vehicles"]:
            paths[role] = (raw_paths[role], imp[role])

        # 修正要素を適用（速度超過・信号無視・一時停止無視）
        entries = {"victim": v_entry, "opponent": o_entry}
        speeds = {role: scenario["vehicles"][role]["speed_mps"]
                  for role in scenario["vehicles"]}
        signalized = scenario.get("junction_type") == "signalized"
        hud, signal_targets, signal_hud = setup_modifiers(
            scenario["id"], world, mods, entries, speeds, actors, signalized=signalized,
            fault_cell=fault_cell)

        # 開始位置に車両＋カメラを配置
        dt = 1.0 / fps
        t_col = scenario["sim"]["collision_at_s"]
        for role, spec in scenario["vehicles"].items():
            path, s_imp = paths[role]
            s0 = s_imp - speeds[role] * t_col      # t=0 の arc-length
            loc, yaw = path_point_at(path, s0)
            v = spawn_vehicle(world, spec, loc, yaw)
            cam, q = attach_dashcam(world, v, fov=fov)
            vehicles[role] = v
            cams[role] = (cam, q)
            d = os.path.join(out_dir, FRAMES_SUBDIR, scenario["id"], role)
            os.makedirs(d, exist_ok=True)
            # 既存フレーム掃除
            for f in os.listdir(d):
                if f.endswith(".png"):
                    os.remove(os.path.join(d, f))
            # HUD用に自車速度(km/h)をサイドカーに記録（ffmpegで焼き込み）
            with open(os.path.join(d, "speed_kmh.txt"), "w") as fp:
                fp.write(str(hud[role]))
            # 自車前方信号の状態HUD（黄進入等）。無ければファイルを残さない（掃除）。
            sig_path = os.path.join(d, "signal_state.txt")
            if role in signal_hud:
                with open(sig_path, "w") as fp:
                    fp.write(signal_hud[role])
            elif os.path.exists(sig_path):
                os.remove(sig_path)

        world.tick()  # 初期化tick

        # --- SIGDEBUG: victim正対信号を投影サンプルして色をCSVに出す（検証専用） ---
        sig_debug = os.environ.get("SIGDEBUG") == "1" and signalized
        vlight_loc = None
        dbg_fp = None
        if sig_debug:
            vls = lights_facing_entry(world, entries["victim"])
            if vls:
                # 最寄りの正対ヘッドを採用。灯体中心やや上をサンプル対象に。
                ve = entries["victim"].transform.location
                vl = min(vls, key=lambda tl: tl.get_transform().location.distance(ve))
                l = vl.get_transform().location
                # CARLAの信号アクター原点はポール基部。レンズは頭上数mにあるため
                # 基部と上端(+7m)の2点を投影し、その垂直区間を走査してレンズを掴む。
                vlight_loc = carla.Location(x=l.x, y=l.y, z=l.z)
                dbg = os.path.join(out_dir, FRAMES_SUBDIR, scenario["id"], "victim", "sigdebug.csv")
                dbg_fp = open(dbg, "w")
                dbg_fp.write("frame,u,v,depth,R,G,B,hue\n")
                print(f"[sigdebug] victim light @({l.x:.1f},{l.y:.1f},{l.z:.1f}) "
                      f"state={vl.get_state()}")

        n_frames = int(scenario["sim"]["duration_s"] * fps)
        for i in range(n_frames):
            t = i * dt
            # 信号無視: 対象信号を毎tick再適用（group自動制御の上書きを防ぐ）
            for tl, st in signal_targets:
                tl.set_state(st)
            for role, spec in scenario["vehicles"].items():
                path, s_imp = paths[role]
                s0 = s_imp - speeds[role] * t_col
                # 衝突後は停止（impactで固定）
                s = s0 + speeds[role] * min(t, t_col)
                loc, yaw = path_point_at(path, s)
                vehicles[role].set_transform(
                    carla.Transform(loc, carla.Rotation(yaw=yaw)))
            world.tick()
            snap_frame = world.get_snapshot().frame
            for role, (cam, q) in cams.items():
                img = None
                # 現在フレームの画像を取り出す
                while True:
                    try:
                        cand = q.get(timeout=2.0)
                    except queue.Empty:
                        break
                    img = cand
                    if cand.frame >= snap_frame:
                        break
                if img is not None:
                    d = os.path.join(out_dir, FRAMES_SUBDIR, scenario["id"], role)
                    img.save_to_disk(os.path.join(d, f"{i:04d}.png"))
                    if os.environ.get("DBG_PROJ") == "1" and signalized and i == n_frames // 2:
                        ctf = cam.get_transform(); cyaw = ctf.rotation.yaw
                        rows = []
                        for _tl, _st in signal_targets:
                            _l = _tl.get_transform().location
                            _head = carla.Location(_l.x, _l.y, _l.z + 5.5)
                            _p = _project_to_image(ctf, _head, img.width, img.height, fov)
                            if _p and 0 <= _p[0] < img.width and 0 <= _p[1] < img.height and 0 < _p[2] < 80:
                                _face = abs(ang_diff(_tl.get_transform().rotation.yaw, cyaw + 180))
                                rows.append((abs(_p[0] - img.width / 2), int(_p[0]), int(_p[1]),
                                             round(_p[2], 1), int(_tl.id), str(_st).split('.')[-1], int(_face)))
                        with open(os.path.join(d, "proj.csv"), "w") as _f:
                            _f.write("id,u,v,depth,state,face_deg  # sorted by centrality (top=most central); lens visible if face<~90\n")
                            for _c, u, v, dep, tid, st, face in sorted(rows):
                                _f.write(f"{tid},{u},{v},{dep},{st},{face}\n")
                        print(f"[DBG_PROJ] {role}: {len(rows)} lights in frame -> {d}/proj.csv")
                    if sig_debug and role == "victim" and vlight_loc is not None:
                        import numpy as _np
                        ctf = cam.get_transform()
                        base = _project_to_image(ctf, vlight_loc, img.width, img.height, fov)
                        top_loc = carla.Location(vlight_loc.x, vlight_loc.y, vlight_loc.z + 7.0)
                        top = _project_to_image(ctf, top_loc, img.width, img.height, fov)
                        if base and top and base[2] < 90:
                            u = int((base[0] + top[0]) / 2)
                            y0, y1 = sorted((int(top[1]), int(base[1])))
                            arr = _np.frombuffer(img.raw_data, dtype=_np.uint8)
                            arr = arr.reshape(img.height, img.width, 4)  # BGRA
                            # 基部→上端の垂直区間を±6px帯で走査し最も彩度の高い点灯レンズを採る
                            best = (0, 0, 0, -1, 0, 0)
                            for yy in range(max(0, y0 - 2), min(img.height, y1 + 3)):
                                for xx in range(max(0, u - 6), min(img.width, u + 7)):
                                    B, G, R = int(arr[yy, xx, 0]), int(arr[yy, xx, 1]), int(arr[yy, xx, 2])
                                    sat = max(R, G, B) - min(R, G, B)
                                    if max(R, G, B) > 100 and sat > best[3]:
                                        best = (R, G, B, sat, xx, yy)
                            R, G, B, _s, bx, by = best
                            mx = max(R, G, B)
                            hue = "gray"
                            if mx > 100:
                                if R > G + 25 and R > B + 25:
                                    hue = "RED"
                                elif G > R + 15 and R < 170:
                                    hue = "GREEN"
                                elif R > 140 and G > 110 and B < R - 40:
                                    hue = "YELLOW"
                            dbg_fp.write(f"{i},{bx},{by},{base[2]:.1f},{R},{G},{B},{hue}\n")
        if dbg_fp:
            dbg_fp.close()
        print(f"[done] {scenario['id']}: {n_frames} frames/viewpoint -> {out_dir}")

    finally:
        for role, (cam, q) in list(cams.items()) if 'cams' in dir() else []:
            try:
                cam.stop(); cam.destroy()
            except Exception:
                pass
        for v in (vehicles.values() if 'vehicles' in dir() else []):
            try:
                v.destroy()
            except Exception:
                pass
        # 修正要素で生成したprop(標識等)を破棄
        for a in actors:
            try:
                a.destroy()
            except Exception:
                pass
        # 信号機のfreezeを解除して通常状態へ戻す
        try:
            for tl in world.get_actors().filter("traffic.traffic_light*"):
                tl.freeze(False)
        except Exception:
            pass
        s = world.get_settings()
        s.synchronous_mode, s.fixed_delta_seconds = orig
        world.apply_settings(s)


def list_junctions(map_name, host, port):
    client = carla.Client(host, port)
    client.set_timeout(30.0)
    world = client.load_world(map_name)
    for jid, j, center in all_junctions(world):
        entries = junction_entries(j)
        yaws = sorted(round(e[2]) for e in entries)
        print(f"junction {jid} center=({center.x:.0f},{center.y:.0f}) "
              f"entries={len(entries)} yaws={yaws}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=["A_intersection", "B_right_turn", "C_signal_crossing"])
    ap.add_argument("--out", default="../videos")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--list-junctions", action="store_true")
    ap.add_argument("--map", default=None,
                    help="撮影マップを上書き（無信号マップでの撮り直し用。未指定なら scenarios_sim._MAP）")
    ap.add_argument("--require-unsignalized", action="store_true",
                    help="信号機が実在しない交差点のみを選ぶ（無信号マップでの撮り直し用）")
    ap.add_argument("--junction-xy", default=None,
                    help="狙い撮りする交差点の中心座標 'X,Y'（巨大交差点回避。find_uncontrolledの座標を渡す）")
    ap.add_argument("--fault-cell", default="mixed", choices=["innocent", "mixed", "main_fault"],
                    help="過失構造セル。judicial_map の cell_overrides（C③=赤信号進入）の解決に使う")
    ap.add_argument("--mods", default="",
                    help="judicial_mapの修正要素キーをカンマ区切りで（例: opp_stop_violation,victim_speeding）")
    ap.add_argument("--fov", type=int, default=95,
                    help="ドラレコの水平画角(度)。既定95=狭角/130前後=広角(study3被害者動画)。")
    args = ap.parse_args()

    if args.list_junctions:
        list_junctions(args.map or "Town10HD_Opt", args.host, args.port)
        return
    mods = [m.strip() for m in args.mods.split(",") if m.strip()]
    from scenarios_sim import SCENARIOS
    scenario = dict(SCENARIOS[args.scenario])
    if args.map:
        scenario["map"] = args.map   # デプロイ設定(_MAP)を書き換えずに撮影マップだけ上書き
    target_xy = None
    if args.junction_xy:
        xs, ys = args.junction_xy.split(",")
        target_xy = (float(xs), float(ys))
    run(scenario, os.path.abspath(args.out), args.host, args.port, mods,
        require_unsignalized=args.require_unsignalized, target_xy=target_xy, fov=args.fov,
        fault_cell=args.fault_cell)


if __name__ == "__main__":
    main()
