# Chat-first workflow setup

The assistant asks the questions and uses the existing Gluons tools to save answers, prepare a connection, and link a draft. Gluons owns the business context, progress, authorization, and execution. The owner visits a browser for consent and approvals that require it.

## Start or resume in chat

1. Call `start_workflow_setup` with `start_business` or `delegate_workflow`, using a stable idempotency key for that attempt. For an existing session, use `list_workflow_setups` and `get_workflow_setup`.
2. Ask the current question in plain language. Confirm any suggestion before using `answer_workflow_setup`. Each answer carries the current version. If another chat changed the setup, reload before applying a correction. Once a plan or automation is linked, revise a proposed workflow in a new setup.
3. Read back the proposed responsibilities and steps. For a launch plan, call `create_business_launch_plan`, then `link_workflow_draft`. For order intake, inspect `multichannel_order_entry`, create the automation with the supported configuration, and link it. Order intake can proceed as a manual trial or start a Gmail read-only connection handoff.
4. For Gmail, offer to reuse a confirmed account selected by the owner or call `prepare_workflow_gmail` for browser consent. Refresh the setup after authorization. The owner still selects the authorized mailbox labels under **Business channels** before background intake runs. The connection grants no send or payment authority.
5. Use `start_trial`, `submit_order_trial_case`, and the trial report to review a synthetic sample. Use `get_task_status`, `get_task_evidence`, and `pause_task` to monitor and control the automation. The existing policy tools send consequential authority to the owner for separate approval.

These tools require a connected owner for the selected business. Workflow setup reads require `gluons.read`, drafts and answers also require `gluons.plan`, and preparing or selecting Gmail requires `gluons.connections`. The existing permissions control task execution and pause separately.

## Review inventory reordering in chat

1. Choose `inventory_reorder` in an existing workflow setup. Create the suggested `inventory_reorder` automation and link it as the workflow draft.
2. Ask the owner to confirm the stock location, currency, and rules for each SKU. Save the supplier, reorder threshold, target stock, pack size, quantity limit, and optional unit estimate with `configure_inventory_reorder`.
3. Start a replay or shadow trial. Use `submit_inventory_trial_case` with the snapshot location, observation time, and each item's available and incoming units. Identify whether the snapshot is synthetic or owner supplied. Retry an identical submission with the same key.
4. Use `get_task_run` to show the saved result. Draft lines are grouped by supplier and priced from the configured estimates. Missing or duplicated observations, wrong locations, missing prices, and quantities above the draft limit stay visible for review. Each run uses the rule values captured when the snapshot was submitted.

The trial combines stock evaluation with supplier draft preparation using checkpoints in the existing runtime. It takes supplied counts and creates reviewable results inside Gluons. A live rollout needs an authoritative stock feed, a selected purchase order provider, and an owner-authorized action with independent readback.

### Link a separate procurement review

For hardware inventory, prepare a separate `hardware_procurement` automation. Show the owner the source and destination and confirm that estimated reorder lines should flow to this review. Start both in the same shadow or historical replay mode, then use `configure_trial_handoff` with the inventory automation ID, its current version and the procurement automation ID.

After a successful inventory review, `get_task_run` returns the downstream run ID or the reason the handoff was skipped. Read the procurement run for its draft and source reference. Inventory exceptions stay with the source review. The link handles purchasing needs and estimates and leaves quotes, supplier contact and order placement for separate verification. Set the destination to `null` to unlink future completions. Pausing either automation retains its existing pause behavior.

### Fictional pilot

**Demo Workshop Supply** uses a synthetic Gmail handoff and the local ERP simulator for its order-entry trial. Its reorder trial takes manually supplied stock counts for **Main warehouse** and values draft quantities in INR. The shadow run uses four fixture SKUs. It groups 18 units of BEANS and 6 units of CUPS into one supplier draft estimated at ₹81.30, and flags LIDS for exceeding its quantity limit and BAGS for a missing price.

Run the three local MCP scenarios from the repository root:

```powershell
$env:GLUONS_TEST_CLUSTER = 'demo-workshop-supply'
.\.venv\Scripts\python.exe -m pytest apps/api/tests/test_mcp_tools_contract.py -k 'chat_reorder or chat_gmail_handoff or chat_order_draft'
.\.venv\Scripts\python.exe -m pytest apps/api/tests/test_order_vertical.py -k supervised_order_writes_once
```

The fictional records support local acceptance. A real mailbox or hosted assistant and a real stock or purchase order system need separate verification and authorization.

## Capability limits

- Launch intake covers the initial product, market, supplier, and storefront decisions. It produces a planning draft. Provider access, public store launch, and money movement have their own gates.
- Existing-process intake supports order-entry and inventory-reorder trials. Other processes currently end in a planning-only result.
- Decision preferences remain separate from grants. Provider side effects require an explicit owner-approved policy. The owner can sign an eligible standing spending allowance with a passkey after reviewing its action, connection, currency and limits. The assistant can request that review in chat.
- Email-code sign-in and spending passkeys have passed GitHub tests. Hosted email delivery and real-device passkey acceptance are pending. The inventory-to-procurement handoff supports one pair of draft-only automations. General composition remains pending.
- An authorized HTTPS environment, a supported assistant host, and explicitly approved provider access are needed for live acceptance. Local MCP, provider doubles, and the ERP simulator supply separate evidence.

## Local verification on November 21, 2026

- Full backend regression passed with 855 tests on the implementation snapshot before the final trial retry and Gmail scope checks. The final focused MCP, setup, and order suite passed all 49 tests against local PostgreSQL and the ERP simulator.
- The existing desktop and mobile Playwright journeys passed on retry. An initial attempt timed out during recovery of the browser test database, and the next attempt stopped during Next.js startup.
- Ruff and traceability passed. The MCP order trial processed a synthetic event, retained one run after a retry, and produced zero external ERP writes. The Gmail test prepared a read-only handoff with a provider double and did not contact Google.
- The inventory extension passed 58 focused backend tests, including supplier grouping, exception handling, replay with pinned rules, and owner-only export. All three fictional-pilot MCP scenarios passed in a separate local run.
- The full inventory regression run passed 860 tests. One rate-limit test crossed its time window and two tests timed out during ERP simulator reset. The three affected tests passed on a fresh database after fixing the test clock and giving the reset endpoint more time. The full suite was run before those test-fixture changes.
