# Incident: Blob trigger not firing on file upload

| | |
|---|---|
| **Date** | 2026-10-18 (all times UTC) |
| **Function App** | `noman-blob-func-ci-261018` (Linux, Python 3.11, **Flex Consumption**) |
| **Resource group** | `noman-blob-rg-2610181310595` |
| **Storage account / container** | `nomanblob2610181310595` / `mycontainer` |
| **Symptom** | Files uploaded to `mycontainer` did not run `blob_trigger` |
| **Status** | ✅ Resolved at 15:32 (deployment `526fe4ec…`). Verified by successful runs at 15:32:52 and 15:35:35 |

---

## 1. Summary

Uploads to `mycontainer` did not run the function. Two independent problems, at
different times, caused this:

1. **No Event Grid subscription for the early uploads.** On Flex Consumption a blob trigger
   only runs when Event Grid pushes a `BlobCreated` event to the function's webhook. The
   first two files were uploaded before the subscription `blob-trigger-sub` existed, so no
   event was ever sent for them.
2. **The deployed code dropped `source="EventGrid"`.** A deployment at 15:16:44 shipped
   the decorator without `source="EventGrid"`, which makes it a polling trigger. Flex
   Consumption does not support polling blob triggers, so the host marked the function as
   *in error* and nothing ran until a corrected build was deployed at 15:32:24.

**Fix:** restore `source="EventGrid"` in `function_app.py` and redeploy. The infrastructure
(Event Grid provider, system topic, subscription, app settings, container) was correct
throughout.

---

## 2. Background: how blob triggers work on Flex Consumption

A blob trigger can detect new blobs in two ways:

| Mode | Decorator | Mechanism | Flex Consumption |
|---|---|---|---|
| **Polling** (default) | no `source` | The host continuously scans container logs for new blobs | ❌ Not supported |
| **Event Grid** | `source="EventGrid"` | Storage pushes `BlobCreated` events to `/runtime/webhooks/blobs` | ✅ Required |

Flex Consumption scales to zero, with no instance permanently running to do the scanning,
so Microsoft only allows the push model there. The Event Grid model has three parts,
and **all three** must be in place:

```
Code: source="EventGrid"   ─┐
Event Grid subscription    ─┼─►  upload → BlobCreated → webhook → function runs
Microsoft.EventGrid provider┘
```

If any part is missing, uploads fail silently: there's no error on the upload, and the
function just never runs.

---

## 3. Timeline

| Time (UTC) | Event | Evidence |
|---|---|---|
| 14:11:48 | Deployment `9187a3f9…` (core tools, remote build) | `kudu-state.json` |
| **14:30:37** | `e-commerce-hld-desgn.png` uploaded | blob `creationTime` |
| 14:40:44 | Deployment `1a440a83…` | `kudu-state.json` |
| **14:43:15** | `Azure_Functions_Python_Blob_Trigger_Guide.docx` uploaded | blob `creationTime` |
| 15:11:36 | Local `function_app.py` saved with `source="EventGrid"` | file mtime |
| 15:12:13 | Host starts, finds `Host.Functions.blob_trigger`, registers the webhook | App Insights |
| **15:13:57** | Event Grid subscription `blob-trigger-sub` **created** | system topic metadata |
| 15:14:03 | *"perform handshake with eventGrid for function: Host.Functions.blob_trigger"* | App Insights |
| **15:14:51** | ✅ `trigger-test.txt`: *"New blob detected(EventGrid)"*, *"Blob trigger fired"*, 23 bytes, Succeeded (691 ms) | App Insights |
| 15:16:44 | **Deployment `d231e2a0…`**, package `function_app.py` has **no** `source=` | downloaded package |
| **15:16:47** | ❌ *"The 'blob_trigger' function is in error: The Flex Consumption SKU only supports EventGrid as the source for BlobTrigger functions."* | App Insights (severity 3) |
| 15:17:39 | Blob listener stopped, JobHost stopped | App Insights |
| 15:20:25, 15:25:16 | Same *"is in error"* message on each host restart | App Insights |
| 15:22:39 | Test upload `trigger-test-2.txt`: no execution | App Insights |
| ~15:30 | Root cause found: local file **and** active package both lack `source="EventGrid"` (byte-identical); `source="EventGrid"` restored locally | diff of local vs. package |
| **15:32:24** | **Deployment `526fe4ec…`**, package contains `source="EventGrid"` | downloaded package |
| 15:32:27 | Host finds `Host.Functions.blob_trigger`, no error | App Insights |
| **15:32:52** | ✅ `e-commerce-hld-desgn.png` (re-uploaded): Succeeded (2097 ms) | App Insights |
| 15:34:38 | ⚠️ Transient *"Process reporting unhealthy"* (readiness check during a host restart) | App Insights (severity 2) |
| **15:35:35** | ✅ `Still Life with Laptop and Coffee Mug … .png`: Succeeded (3612 ms) | App Insights |

---

## 4. Root-cause analysis

### Cause 1: Event Grid subscription created after the first uploads
- With `source="EventGrid"` the function relies entirely on Event Grid. The subscription
  `blob-trigger-sub` was created at **15:13:57**.
- `e-commerce-hld-desgn.png` (14:30) and the `.docx` (14:43) were uploaded **before** that,
  so storage had no subscriber to notify.
- Event Grid does **not** replay past events, so those blobs would never trigger on their own.
- Topic metrics for the period showed only **1 event published / 1 delivered / 0 failed**:
  the 15:14 `trigger-test.txt` upload. That confirms nothing was sent for the earlier files.

### Cause 2: Polling-mode code deployed to a Flex Consumption app
- The 15:16:44 deployment's `function_app.py` was:
  ```python
  @app.blob_trigger(
      arg_name="myblob",
      path="mycontainer/{name}",
      connection="a5f5f0_STORAGE",
  )                                  # ← no source="EventGrid"
  ```
- With no `source`, the binding defaults to **polling**, and the Flex host refuses to load it:
  > The 'blob_trigger' function is in error: The Flex Consumption SKU only supports
  > EventGrid as the source for BlobTrigger functions.
- The line had been removed locally to test a suggestion that polling would be simpler.
  That advice is only valid for **local** runs or non-Flex plans; on Flex it breaks the function.
- **Why it was easy to miss:** the error appears only in host logs (App Insights / Log
  stream), not in the deploy output. Event Grid also reported successful delivery: the
  webhook endpoint still returned 2xx because the host was running, even though the
  function itself was disabled.

### Contributing factor: "published" didn't mean "deployed"
When the redeploy was first reported, the `app-package-*` container showed **no new
package** after 15:16:44. Either the publish didn't complete, or it republished the same
code, because the local file still lacked the line. In both cases the active package was
unchanged. The newest zip in `app-package-*` (and `ActiveDeploymentId` in `kudu-state.json`)
is the reliable source of truth.

---

## 5. What was verified as correct (ruled out)

| Check | Result |
|---|---|
| Subscription | `Azure subscription 1`, enabled |
| `Microsoft.EventGrid` resource provider | **Registered** |
| Hosting plan | `FlexConsumption`, Python 3.11, state Running |
| Event Grid system topic | `nomanblob2610181310595-topic`, provisioning Succeeded |
| Event subscription `blob-trigger-sub` | WebHook → `…/runtime/webhooks/blobs?functionName=Host.Functions.blob_trigger&code=***`; type `BlobCreated`; subject prefix `/blobServices/default/containers/mycontainer/`; retry 30 attempts / 24 h |
| Container `mycontainer` | Exists |
| App setting `a5f5f0_STORAGE` | Present in Azure |
| Binding as deployed | `blobTrigger`, `path=mycontainer/{name}`, `connection=a5f5f0_STORAGE` |
| `host.json` extension bundle | `[4.*, 5.0.0)` (supports the Event Grid source) |

---

## 6. Resolution

1. Restored `source="EventGrid"` in `function_app.py`:
   ```python
   @app.blob_trigger(
       arg_name="myblob",
       path="mycontainer/{name}",
       connection="a5f5f0_STORAGE",
       source="EventGrid",
   )
   ```
2. Redeployed (`func azure functionapp publish noman-blob-func-ci-261018`), creating
   package `526fe4ec…` at 15:32:24.
3. Verified:
   - The downloaded package's `function_app.py` contains `source="EventGrid"`.
   - No *"is in error"* messages after 15:32.
   - Uploads at 15:32 and 15:35 logged *"Executed 'Functions.blob_trigger' (Succeeded…)"*.

---

## 7. How the diagnosis was done (reusable commands)

```bash
# Git Bash needs this so /subscriptions/... IDs aren't rewritten
export MSYS_NO_PATHCONV=1
RG=noman-blob-rg-2610181310595; SA=nomanblob2610181310595; APP=noman-blob-func-ci-261018
SUB=$(az account show --query id -o tsv); TOPIC=$SA-topic

# 1. Provider
az provider show --namespace Microsoft.EventGrid --query registrationState -o tsv

# 2. Hosting plan
az rest --method get --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.Web/sites/$APP?api-version=2023-12-01" \
  --query "{sku:properties.sku, runtime:properties.functionAppConfig.runtime}"

# 3. Deployed functions, app settings, containers
az functionapp function list -n $APP -g $RG -o json
az functionapp config appsettings list -n $APP -g $RG --query "[].name" -o tsv
az storage container list --account-name $SA --auth-mode key --query "[].name" -o tsv

# 4. Event Grid subscription (+ full URL; mask the key before sharing)
az eventgrid system-topic event-subscription list -g $RG --system-topic-name $TOPIC -o json
az eventgrid system-topic event-subscription show -g $RG --system-topic-name $TOPIC -n blob-trigger-sub \
  --include-full-endpoint-url --query destination.endpointUrl -o tsv | sed -E 's/code=[^&]+/code=***/'

# 5. Did Event Grid publish/deliver? (last 24 h)
TID=/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.EventGrid/systemTopics/$TOPIC
for m in PublishSuccessCount MatchedEventCount DeliverySuccessCount DeliveryAttemptFailCount DroppedEventCount; do
  az monitor metrics list --resource $TID --metrics $m --offset 24h --interval 1h --aggregation Total -o table
done

# 6. Host logs: executions and errors
az monitor app-insights query -g $RG --app $APP --offset 24h --analytics-query \
  "traces | where severityLevel >= 3 or message has_any ('Executed','Blob name','handshake') | project timestamp, severityLevel, message | order by timestamp desc" -o table

# 7. What code is ACTUALLY deployed? (Flex stores each deploy as a zip)
PKG=$(az storage container list --account-name $SA --auth-mode key --query "[?starts_with(name,'app-package')].name | [0]" -o tsv)
az storage blob list --account-name $SA --auth-mode key -c $PKG --query "[].{n:name,t:properties.lastModified}" -o table
az storage blob download --account-name $SA --auth-mode key -c $PKG -n kudu-state.json -f kudu-state.json   # see ActiveDeploymentId
az storage blob download --account-name $SA --auth-mode key -c $PKG -n <ActiveDeploymentId>.zip -f pkg.zip
unzip -p pkg.zip function_app.py | grep -n "source="
```

---

## 8. Lessons learned and prevention

| # | Lesson | Action |
|---|---|---|
| 1 | On Flex Consumption, `source="EventGrid"` is **mandatory** for blob triggers | Keep the warning comment in the README. Never "simplify" by removing it |
| 2 | Event Grid only notifies about uploads made **after** the subscription exists | Create the subscription right after the first deploy. Re-upload older blobs if needed |
| 3 | Function load errors don't show up in deploy output | After every deploy, check App Insights / Log stream for *"is in error"* |
| 4 | A "published" app isn't necessarily running the new code | Confirm a new zip in `app-package-*` and the new `ActiveDeploymentId` |
| 5 | Event Grid "delivered" doesn't mean the function ran | Check for `Executed 'Functions.blob_trigger' (Succeeded…)` in logs |
| 6 | Local runs differ from Flex | Event Grid can't reach `localhost`. Polling works locally, but don't deploy polling code to Flex |
| 7 | App Insights lags 1–3 minutes | Use Log stream for quick checks before deciding nothing ran |

### Optional hardening
- Add a **dead-letter destination** to `blob-trigger-sub`, so events that can't be delivered
  are kept for inspection instead of dropped after 24 hours.
- Add an App Insights **alert** on traces containing `is in error`.
- Add a pre-deploy check (script or CI step) that fails if `function_app.py` has a
  `blob_trigger` without `source="EventGrid"`:
  ```powershell
  if (-not (Select-String -Path function_app.py -Pattern 'source="EventGrid"' -Quiet)) { throw "Missing source=EventGrid" }
  ```
