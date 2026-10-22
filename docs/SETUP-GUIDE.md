# MatchItUp — step-by-step setup guide (from zero)

You need: a web browser, your GitHub account, and (for Part D) a Microsoft account with a phone and a debit/credit card for Azure verification. Everything else runs in the cloud, so nothing has to be installed on your computer.

Time needed: Parts A–B about 30 minutes; Part D about 45 minutes.

---

## Part A — Put the project on GitHub and run it (20 min)

1. Go to **https://github.com/new**.
   - Repository name: `matchitup`
   - Visibility: **Public** (the hackathon requires a public repository)
   - Tick **Add a README file**
   - Click **Create repository**.
2. On the new repository page click the green **Code** button → **Codespaces** tab → **Create codespace on main**. A VS Code editor opens in your browser (first start takes 1–2 minutes).
3. Drag `matchitup.zip` from your computer into the **Explorer** panel on the left of the Codespace.
4. Click in the **Terminal** at the bottom and paste these lines, then press Enter:
   ```bash
   unzip -o matchitup.zip -d /tmp/m && cp -r /tmp/m/matchitup/. . && rm matchitup.zip
   pip install -r requirements-dev.txt
   pytest -q
   ```
   You should see all tests pass.
5. Save the code to GitHub:
   ```bash
   git add -A
   git commit -m "MatchItUp: live explainable match intelligence"
   git push
   ```
6. Start the app:
   ```bash
   uvicorn app.main:app --reload
   ```
   A pop-up says the app is running on port 8000 → click **Open in Browser**. Click **Kick off**.

   The badge at the top right says **Template mode**. Everything works; the narratives come from grounded templates until you connect a Microsoft Foundry model in Part D.

7. (Optional) To use the preconfigured environment (Azure CLI, Copilot extension), press **F1** → **Codespaces: Rebuild Container**.

> Each time you come back: github.com/parish01/matchitup → **Code → Codespaces** → open the existing codespace → run `uvicorn app.main:app --reload`.

---

## Part B — Turn on GitHub Copilot (free) (5 min)

1. Go to **https://github.com/settings/copilot** and enable **Copilot Free**.
2. In the Codespace, open the **Copilot Chat** panel (chat icon in the left or top bar).
3. Use it for your own improvements, for example:
   - "Explain how `matchitup/engine.py` detects a momentum swing."
   - "Add a new key moment type `counter_attack` to the engine, with a test."
   - "Change the colour of the home team to green."

Using Copilot for your changes is worth mentioning in the pitch and the video, since it is one of the hackathon's recommended technologies.

---

## Part C — (removed)

GitHub Models was retired by GitHub on 30 July 2026, so the free GitHub route no longer exists. The AI agents run on **Microsoft Foundry** instead (Part D). Until then the app works fully in template mode.

---

## Part D — Deploy on Azure with Microsoft Foundry (45 min)

This is what turns it into a production-style Microsoft solution for the judges.

### D1. Create an Azure account
1. Go to **https://azure.microsoft.com/free** and click **Try Azure for free** / **Start free**.
2. Sign in with a Microsoft account (or create one), then complete phone and card verification. Check the page for the current free credit offer. Indian cards need international/online transactions enabled.
3. Wait until the Azure portal (**https://portal.azure.com**) opens.

### D2. Deploy everything with one script
In the Codespace terminal:
```bash
az login --use-device-code
```
Open the link it shows, enter the code, sign in. (If the terminal says `az: command not found`, do Part A step 7 first, or run `curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash`.) Then:
```bash
bash infra/deploy-aca.sh
```
The script creates, in the resource group `rg-matchitup`:
- a **Microsoft Foundry** resource with a `gpt-4.1-mini` model deployment
- **Application Insights** for traces
- an **Azure Container App** built from your code, with a **managed identity** that has permission to call the model (no keys)

At the end it prints your public URL, e.g. `https://matchitup.<something>.swedencentral.azurecontainerapps.io`. Open it; within a few minutes the badge shows **AI live · azure**.

**If the model step fails** (model or version not available in the region): open **https://ai.azure.com**, open the model catalogue, find an available small chat model and its version, then re-run, for example:
```bash
MODEL_NAME=gpt-4.1-mini MODEL_VERSION=2025-04-14 LOCATION=eastus2 bash infra/deploy-aca.sh
```

### D3. Use the Azure model in the Codespace too (optional)
The script prints three lines for `.env` and one `az role assignment` command. Run that command, add the three lines to `.env`, and restart the app.

### D4. Costs and clean-up
- The app keeps one small replica running during judging, and model calls cost very little at this scale. Check **Cost Management** in the portal occasionally.
- After the hackathon, delete everything with:
  ```bash
  az group delete -n rg-matchitup --yes
  ```

---

## Part E — Record the demo video (under 2 minutes)

Follow `docs/demo-video-script.md`.
- Record with **Clipchamp** (free with Windows) or the **Xbox Game Bar** (Windows + G).
- Use no copyrighted music and no real club logos.
- Upload to YouTube as **Public**, and copy the link.

---

## Part F — Submit on Innovation Studio

On your project page, fill in:
- **GitHub repository:** `https://github.com/parish01/matchitup`
- **Demo video:** your YouTube link
- **Project pitch:** copy from `docs/pitch.md`
- **Technologies:** Microsoft Foundry (Azure OpenAI), Azure Container Apps, Microsoft Entra ID managed identity, Application Insights / OpenTelemetry, GitHub Codespaces, GitHub Actions, GitHub Copilot
- Optionally the live URL from Part D.

Submit well before the deadline and check that the repository is public by opening it in a private/incognito browser window.
