# Project pitch (copy into Innovation Studio)

## One-liner
MatchItUp turns a live feed of synthetic football events into explainable, personalised match intelligence — real-time stats, "why it matters" narratives, broadcast-ready overlays and automated recaps — so every match feels made for every fan.

## Elevator pitch (about 150 words)
Live football graphics are one-size-fits-all: analysts want depth, casual fans want meaning, and global audiences want it in their own language. MatchItUp ingests synthetic match events as they happen, and a deterministic engine computes the numbers — pass difficulty, ball and shot speed, xG, momentum, control vs chaos, pressing, player speed and distance — and detects key moments. A team of AI agents on Microsoft Foundry then explains why each moment matters (Narrator), rewrites it for each viewer's mode, language, favourite club and favourite player (Personalizer), and writes half-time and full-time recaps (Recap). A Verifier checks every generated number against the facts, with a reflection retry and grounded fallback, and every card carries a receipt of how it was made. Output is timed, machine-readable overlay JSON for broadcast partners plus a live web experience. Viewers can also ask a tool-calling Analyst agent questions mid-match.

## Microsoft and Azure technologies
- Microsoft Foundry (Azure OpenAI) — Narrator, Personalizer, Recap and Analyst agents
- Microsoft Entra ID managed identity — keyless access from the app to Foundry
- Azure Container Apps — hosting with autoscaling and revisions
- Azure Monitor Application Insights + OpenTelemetry — per-agent traces
- GitHub Codespaces, GitHub Actions CI, GitHub Copilot

## Problem it solves
Broadcasters and streaming platforms can produce one explainable, verified insight per moment and deliver it as many genuinely different experiences — analyst or casual, English or Hindi, home or away fan — automatically and at live speed, without risking invented statistics on screen.
