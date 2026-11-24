# Evidence-Anchored AI Mediation — code, materials and aggregate results

Materials for the preregistered experiment reported in
**"People Lied; the Settlements Did Not: A Preregistered Ground-Truth Experiment on Evidence-Anchored AI Mediation"** (manuscript in preparation).

Participants acted as one party to a simulated traffic accident, instructed their own AI agent over several rounds,
and a neutral AI mediator proposed a fault ratio computed only from evidence-verified facts. Every accident has a known
true fault ratio, so both misreporting and outcome error can be measured.

- Preregistration (OSF): https://osf.io/m5qhz/
- Ethics approval: SDM Research Ethics Committee, approval no. SDM-2026-E064

## Contents

| Path | What it is |
|---|---|
| `backend/` | Experiment server (AWS Lambda, Python 3.11). `judicial_map.py` is the single source of truth for the legal definitions: base fault ratios, modifiers, ground truth, true ratio, and the deterministic deception classification. `agent.py` holds the prompts for the agent, opponent, mediator and advice roles. `instruments.py` holds the questionnaires. |
| `frontend/index.html` | Participant interface (single-file React app, Japanese), including the consent form and debriefing. |
| `analysis/confirmatory.py` | The preregistered confirmatory analysis (H1–H4, exclusions, sensitivity analyses). |
| `analysis/sens_paste.py` | Preregistered sensitivity analysis excluding participants with paste attempts. |
| `analysis/recode_llm.py` | Re-coding of statements by independent language models and agreement (kappa). |
| `analysis/explore_*.py` | Exploratory (not preregistered) analyses. |
| `analysis/results/` | Text output of every analysis above, as run on the frozen data set (2026-11-24). |
| `materials/videos/` | Stimulus videos (CARLA 0.9.15; left-hand traffic). `*_victim.mp4` were shown to participants; `*_opponent.mp4` were not. |
| `materials/coding_*.{md,json}` | Instructions and codebooks given to the independent coders. |
| `simulation/` | Scripts that generated the stimulus videos. |
| `docs/prereg_specifications_and_deviations_ja.md` | Full log of how the registered plan was made concrete and the deviation from it (Japanese). |

## Data availability

**Participant-level data (responses and written statements) are not included.** The consent form told participants that
access to their responses is limited to the principal investigator and the researcher conducting the study, and that
results are published only in aggregate or non-identifiable form. This repository therefore contains code, materials
and aggregate output only. The analysis scripts expect a data dump that is not distributed here.

## Notes

- Most code comments, prompts and the interface are in Japanese.
- Deployment-specific settings (API endpoint, keys) are not included.
- No licence has been chosen yet; please contact the authors before reuse.

---

## 日本語の概要

事前登録した実験（OSF https://osf.io/m5qhz/ ）の、コード・材料・集計結果です。
参加者ごとの回答と発言文は、同意説明で閲覧者を研究責任者と実施者に限ると定めたため、含めていません。
