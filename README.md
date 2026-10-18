# Azure Functions – Blob Upload Trigger (Python, Flex Consumption)

A learning project: a Python Azure Function that runs automatically whenever a file
is uploaded to the `mycontainer` blob container, and logs the blob's name and size.

Because the app is hosted on the **Flex Consumption** plan, the blob trigger is
driven by **Azure Event Grid** (push), not by the classic polling blob trigger.

> Troubleshooting history for this project: [docs/INCIDENT-blob-trigger-not-firing.md](docs/INCIDENT-blob-trigger-not-firing.md)

---

## 1. Architecture

```
 ┌──────────────┐   upload    ┌────────────────────────────┐
 │ User / CLI / │ ──────────► │ Storage account            │
 │ Portal       │             │ nomanblob2610181310595     │
 └──────────────┘             │   └─ container: mycontainer│
                              └─────────────┬──────────────┘
                                            │ Microsoft.Storage.BlobCreated
                                            ▼
                              ┌────────────────────────────┐
                              │ Event Grid system topic    │
                              │ nomanblob2610181310595-topic│
                              │   └─ subscription:         │
                              │      blob-trigger-sub      │
                              │      filter: /containers/  │
                              │              mycontainer/  │
                              └─────────────┬──────────────┘
                                            │ HTTPS POST (webhook)
                                            ▼
        https://noman-blob-func-ci-261018.azurewebsites.net/runtime/webhooks/blobs
                         ?functionName=Host.Functions.blob_trigger&code=<blobs_extension key>
                                            │
                                            ▼
                              ┌────────────────────────────┐
                              │ Function App (Flex Cons.)  │
                              │ noman-blob-func-ci-261018  │
                              │   └─ blob_trigger (Python) │
                              └─────────────┬──────────────┘
                                            │ logs
                                            ▼
                              ┌────────────────────────────┐
                              │ Application Insights       │
                              │ noman-blob-func-ci-261018  │
                              └────────────────────────────┘
```

### Azure resources

| Resource | Name | Purpose |
|---|---|---|
| Resource group | `noman-blob-rg-2610181310595` | Holds everything below |
| Storage account | `nomanblob2610181310595` | Uploaded files (`mycontainer`), Functions host state, deployment packages |
| Function App | `noman-blob-func-ci-261018` | Linux, Python 3.11, **Flex Consumption** |
| App Service plan | `ASP-nomanblobrg2610181310595-69c0` | Flex Consumption plan |
| Event Grid system topic | `nomanblob2610181310595-topic` | Emits storage events |
| Event Grid subscription | `blob-trigger-sub` | Sends `BlobCreated` events for `mycontainer/` to the function webhook |
| Application Insights | `noman-blob-func-ci-261018` | Logs and invocation telemetry |

### Containers in the storage account

| Container | Who uses it |
|---|---|
| `mycontainer` | **Your uploads**: the function watches this |
| `app-package-nomanblobfuncci261018-…` | Flex Consumption deployment packages (one zip per deploy, plus `kudu-state.json`) |
| `azure-webjobs-hosts` | Functions host locks and blob receipts |
| `azure-webjobs-secrets` | Function and host keys |

---

## 2. Project layout

```
azure-fucntions/
├── function_app.py        # The function (Python v2 programming model)
├── host.json              # Host config + extension bundle [4.*, 5.0.0)
├── requirements.txt       # azure-functions
├── local.settings.json    # Local-only settings & connection strings (git-ignored, NOT deployed)
├── .funcignore            # Files excluded from the deployment package
├── .gitignore
├── .vscode/               # VS Code tasks (func host start, pip install) & debug config
└── docs/
    └── INCIDENT-blob-trigger-not-firing.md
```

---

## 3. The code

```python
@app.blob_trigger(
    arg_name="myblob",
    path="mycontainer/{name}",      # container/{name} – {name} binds the blob name
    connection="a5f5f0_STORAGE",    # NAME of an app setting holding the connection string
    source="EventGrid",             # REQUIRED on Flex Consumption
)
def blob_trigger(myblob: func.InputStream) -> None:
    logging.info("Blob trigger fired.")
    logging.info("Blob name: %s", myblob.name)
    logging.info("Blob size: %s bytes", myblob.length)
```

| Parameter | Meaning |
|---|---|
| `arg_name` | Python parameter that receives the blob |
| `path` | `container/{name}` pattern; only blobs in `mycontainer` match |
| `connection` | The *name* of an app setting (`a5f5f0_STORAGE`), not the connection string itself |
| `source` | `"EventGrid"` means a push-based webhook. Leaving it out means **polling**, which Flex Consumption **rejects** |

> ⚠️ **Never remove `source="EventGrid"` while this app is on Flex Consumption.**
> If you do, the host logs *"The Flex Consumption SKU only supports EventGrid as the
> source for BlobTrigger functions"* and the function stops running.

---

## 4. End-to-end workflow

### What happens when a file is uploaded
1. A file is uploaded to `mycontainer` (Portal, CLI, Storage Explorer, SDK…).
2. Storage publishes a `Microsoft.Storage.BlobCreated` event to the system topic.
3. Subscription `blob-trigger-sub` matches it (event type + subject prefix
   `/blobServices/default/containers/mycontainer/`).
4. Event Grid POSTs the event to the function app's `/runtime/webhooks/blobs` endpoint
   (authenticated by the `blobs_extension` system key in the URL).
5. The Flex app wakes from zero if needed, reads the blob using `a5f5f0_STORAGE`, and runs
   `blob_trigger`.
6. Logs appear in Log Stream and Application Insights (expect 1–3 minutes of ingestion delay).

> Event Grid only notifies about blobs created **after** the subscription exists, and
> while the function is healthy (with retries for up to 24 hours). Blobs uploaded before
> that are never replayed. Re-upload them if they need processing.

### One-time setup (already done for this project)
```powershell
$RG  = "noman-blob-rg-2610181310595"
$SA  = "nomanblob2610181310595"
$APP = "noman-blob-func-ci-261018"

# 1. Event Grid resource provider (often unregistered on new/free subscriptions)
az provider register --namespace Microsoft.EventGrid
az provider show --namespace Microsoft.EventGrid --query registrationState -o tsv   # Registered

# 2. Container for uploads
az storage container create --account-name $SA -n mycontainer --auth-mode key

# 3. App setting used by the trigger's `connection`
$cs = az storage account show-connection-string -n $SA -g $RG -o tsv
az functionapp config appsettings set -n $APP -g $RG --settings "a5f5f0_STORAGE=$cs"

# 4. Deploy the code FIRST (the webhook must exist before Event Grid can validate it)
func azure functionapp publish $APP

# 5. Event Grid subscription to the function's blob webhook
$key = az functionapp keys list -n $APP -g $RG --query "systemKeys.blobs_extension" -o tsv
$endpoint = "https://$APP.azurewebsites.net/runtime/webhooks/blobs?functionName=Host.Functions.blob_trigger&code=$key"
$said = az storage account show -n $SA -g $RG --query id -o tsv
az eventgrid event-subscription create `
  --name blob-trigger-sub `
  --source-resource-id $said `
  --endpoint-type webhook `
  --endpoint $endpoint `
  --included-event-types Microsoft.Storage.BlobCreated `
  --subject-begins-with /blobServices/default/containers/mycontainer/
```
(Portal alternative: **Storage account → Events → + Event Subscription → Endpoint type
*Web Hook*** with the URL above.)

### Day-to-day development loop
1. **Edit** `function_app.py`.
2. **Run locally (optional)** with `func start`, or press F5 in VS Code (it runs `pip install`
   into `.venv`, then `func host start`).
   - Locally, Event Grid **cannot reach `localhost`**, so uploads to Azure won't trigger
     your local function. Use a dev tunnel or ngrok plus a second subscription, or just test
     in Azure.
3. **Check the decorator still has `source="EventGrid"`.**
4. **Deploy:**
   ```powershell
   func azure functionapp publish noman-blob-func-ci-261018
   ```
   or VS Code → Azure panel → right-click the app → *Deploy to Function App*.
   Make sure the output ends successfully. A failed or cancelled publish leaves the old code running.
5. **Test** by uploading a file:
   ```powershell
   az storage blob upload --account-name nomanblob2610181310595 -c mycontainer `
     -n test.txt -f .\test.txt --overwrite --auth-mode key
   ```
6. **Verify** in Function App → *Log stream*, or with the App Insights queries below.

---

## 5. Configuration

### App settings in Azure (Function App → Settings → Environment variables)
| Setting | Purpose |
|---|---|
| `AzureWebJobsStorage` | Host storage (locks, keys, receipts) |
| `a5f5f0_STORAGE` | Connection string used by the blob trigger |
| `DEPLOYMENT_STORAGE_CONNECTION_STRING` | Where Flex stores deployment packages |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Telemetry |

`local.settings.json` is used **only** by `func start` on your machine. It is listed in
`.funcignore` and `.gitignore`, so it is never deployed or committed. It contains your
storage account key, so keep it private. If it leaks, rotate the key (*Storage account →
Access keys → Rotate*) and update both connection-string settings.

### host.json
Uses extension bundle `[4.*, 5.0.0)`, which includes the Storage Blobs extension with
Event Grid source support.

---

## 6. Operations cheat-sheet

```powershell
$RG="noman-blob-rg-2610181310595"; $SA="nomanblob2610181310595"; $APP="noman-blob-func-ci-261018"

# Hosting plan (expect FlexConsumption)
az rest --method get --url "https://management.azure.com/subscriptions/$(az account show --query id -o tsv)/resourceGroups/$RG/providers/Microsoft.Web/sites/$APP?api-version=2023-12-01" --query "properties.sku" -o tsv

# Deployed functions & bindings
az functionapp function list -n $APP -g $RG --query "[].{name:name,bindings:config.bindings}" -o json

# App setting names
az functionapp config appsettings list -n $APP -g $RG --query "[].name" -o tsv

# Event Grid subscription (with full endpoint URL)
az eventgrid system-topic event-subscription show -g $RG `
  --system-topic-name "$SA-topic" -n blob-trigger-sub --include-full-endpoint-url

# Files in mycontainer, with upload time
az storage blob list --account-name $SA -c mycontainer --auth-mode key `
  --query "[].{Name:name, Size:properties.contentLength, Uploaded:properties.creationTime}" -o table

# Files in every container
foreach ($c in (az storage container list --account-name $SA --auth-mode key --query "[].name" -o tsv)) {
  Write-Host "=== $c"
  az storage blob list --account-name $SA -c $c --auth-mode key --query "[].name" -o tsv
}

# Deployment history (one zip per deploy; newest = active)
az storage blob list --account-name $SA --auth-mode key `
  -c (az storage container list --account-name $SA --auth-mode key --query "[?starts_with(name,'app-package')].name | [0]" -o tsv) `
  --query "[].{Name:name, Modified:properties.lastModified}" -o table
```

### Application Insights queries
```powershell
# Recent invocations
az monitor app-insights query -g $RG --app $APP --offset 2h --analytics-query `
  "traces | where message has_any ('Executed','Blob name') | project timestamp, message | order by timestamp desc | take 20" -o table

# Errors / warnings (look for 'is in error')
az monitor app-insights query -g $RG --app $APP --offset 2h --analytics-query `
  "traces | where severityLevel >= 3 | project timestamp, message | order by timestamp desc | take 20" -o table
```

> **Git Bash users:** prefix commands that take `/subscriptions/...` IDs with
> `MSYS_NO_PATHCONV=1`, or Git Bash will rewrite the path and the CLI will fail.

---

## 7. Troubleshooting checklist

Work through these in order when an upload doesn't trigger the function:

| # | Check | How | Fix |
|---|---|---|---|
| 1 | Function loaded without error | App Insights: `severityLevel >= 3`, look for *"is in error"* | Fix the code and redeploy |
| 2 | `source="EventGrid"` is in the **deployed** code | Download the newest zip from `app-package-*` and inspect `function_app.py` | Add it back and redeploy |
| 3 | The deploy actually happened | A new zip and updated `kudu-state.json` in `app-package-*` | Re-run publish and read the output |
| 4 | Event Grid provider registered | `az provider show -n Microsoft.EventGrid` | `az provider register …` |
| 5 | Subscription exists and points at the right function | `--include-full-endpoint-url` shows `functionName=Host.Functions.blob_trigger` | Recreate the subscription |
| 6 | Event Grid delivered the event | Metrics on the system topic: `PublishSuccessCount`, `DeliverySuccessCount`, `DeliveryAttemptFailCount` | Fix the endpoint or key; check that the function is healthy |
| 7 | Uploaded to the right container | Must be `mycontainer` | Upload there, or change `path` and the subscription filter |
| 8 | Uploaded **after** the subscription was created | Compare the blob's `creationTime` with the subscription creation time | Re-upload |
| 9 | `a5f5f0_STORAGE` app setting exists in Azure | `appsettings list` | Add it (local.settings.json isn't deployed) |
| 10 | Waited for logs | App Insights lags 1–3 minutes | Use Log stream for near-real-time logs |

---

## 8. Cost notes (free subscription)
- **Flex Consumption**: pay per execution and GB-s, with a monthly free grant. It scales to zero when idle.
- **Event Grid**: first 100,000 operations per month are free.
- **Storage / App Insights**: a few cents at most for a learning workload.
