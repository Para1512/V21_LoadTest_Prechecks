# High Level Design (HLD)
# V21 PreChecks Automation Portal

| Field | Value |
|-------|--------|
| **Document** | HLD — V21 PreChecks Portal |
| **Purpose** | Automate PET / V21 test preparatory checks before performance testing |
| **Primary users** | QE Performance Engineering |
| **Runtime** | Python Flask backend (`python app.py`) + browser UI |
| **Environment** | VDI (primary), developer workstation via Git sync |

---

## 1. Executive summary

The **V21 PreChecks Automation Portal** consolidates repetitive pre-test activities for Rogers V21 PET into a single web UI. Operators run ordered checks (data loading, disk space, log space, server bounce, health) and trigger support emails to DBA / NPE teams without manual copy-paste across tools.

The portal does **not** replace production change tools; it orchestrates **read checks**, **remediation where automated**, and **notifications** to the right support groups.

---

## 2. System context

```
┌─────────────────┐     HTTP      ┌──────────────────────┐
│  Browser (UI)   │ ◄──────────► │  Flask API (app.py)   │
│  public/        │               │  tasks/*.py           │
└─────────────────┘               └──────────┬───────────┘
                                             │
         ┌───────────────────────────────────┼───────────────────────────────┐
         │                                   │                               │
         ▼                                   ▼                               ▼
  ┌─────────────┐                    ┌─────────────┐                 ┌─────────────┐
  │ VTS 1/2     │                    │ Oracle DB   │                 │ NPE CGI     │
  │ :2244/:2245 │                    │ PET1_CUST   │                 │ npe-services│
  └─────────────┘                    └─────────────┘                 └─────────────┘
         │                                   │                               │
         │                            SMTP (email)                    ReqCentral /
         │                                   │                         JIRA browse
         ▼                                   ▼                               ▼
  ┌─────────────┐                    ┌─────────────┐                 ┌─────────────┐
  │ LG servers  │                    │ DBA / NPE   │                 │ Batch portal│
  │ WMI disk    │                    │ mailboxes   │                 │ NPR bounce  │
  └─────────────┘                    └─────────────┘                 └─────────────┘
```

### 2.1 Configuration

Secrets and environment-specific values live in **`.env`** (not in GitHub). Template: `.env.example`.

Key groups: `ORACLE_*`, `VTS1_URL`, `VTS2_URL`, `SMTP_*`, `NPE_SERVICES_CGI_BASE`, optional `LG_WMI_*`.

---

## 3. Portal sections (top to bottom)

| Section | Description |
|---------|-------------|
| **Email Triggers** | One-click emails for DBA / NPE preparatory work |
| **Automation Tasks** | Numbered tasks 1–6 with RUN / SKIP and expandable output |

Additional Task 1 controls: **Test Oracle DB**, **Oracle Queries** (edit remediation SQL).

---

## 4. Email triggers

### 4.1 HUP Offer Cleanup

| Item | Detail |
|------|--------|
| **UI button** | `HUP Offer Cleanup - Trigger Email` |
| **API** | `POST /api/email/hup-offer-cleanup` |
| **To** | `#RCI_V21 DBA Support` (RCIV21DBASupport@rci.rogers.com) |
| **CC** | QE Performance Engineering |
| **Subject** | Rogers V21 \| PET \| Test Preparatory Activities |
| **Request** | Execute `TRUNCATE TABLE HUP_OFFER` in **PCUST_DB** |

**Intended end-to-end process**

1. Create a **JIRA Business Request (BR)** for the HUP offer cleanup activity.
2. Attach the BR reference to the notification email.
3. Send email to DBA Support so they execute the truncate on PCUST_DB.

**Current implementation**

- Portal sends the SMTP email with the truncate request text.
- **JIRA BR auto-creation and BR attachment are not yet wired in code** — BR is still raised manually (or via separate process) before/after triggering the email.

---

### 4.2 CRM IGW Bounce Mail

| Item | Detail |
|------|--------|
| **UI button** | `CRM IGW Bounce - Trigger Email` |
| **API** | `POST /api/email/crm-igw-bounce` |
| **To** | `#RCI_V21 NPE Support` (RCIV21NPESupport@rci.rogers.com) |
| **CC** | QE Performance Engineering |
| **Subject** | Rogers V21 \| PET \| Test Preparatory Activities |
| **Request** | Reboot **CRM IGW Domain 1** servers |

**Intended end-to-end process**

1. Create a **JIRA BR** for CRM IGW Domain 1 reboot.
2. Attach the BR to the email to NPE Support.

**Current implementation**

- Portal sends the SMTP email with the reboot request.
- **JIRA BR automation / attachment — planned; not yet implemented in portal.**

---

## 5. Task 1 — Data Loading

**Purpose:** Validate Virtual Test System (VTS) test data on VTS1 and VTS2, remediate VTS1 gaps from Oracle, remove stale test columns, and confirm readiness for PET.

**Targets**

| Instance | Host | Port | API base |
|----------|------|------|----------|
| VTS 1 | vm0196ptlrg001 | 2244 | `GET /data/get_page`, `clear_column`, `import_csv` |
| VTS 2 | vm0196ptlrg001 | 2245 | Same pattern |

**Entry point:** `POST /api/tasks/data-loading/run-flow` (Task 1 **RUN**)

### 5.1 High-level flow

```
RUN Task 1
    │
    ▼
┌─────────────────────┐
│ Phase A: Pre-check  │  Minimum counts on VTS1 + VTS2
└─────────┬───────────┘
          │ fail VTS1 columns?
          ▼
┌─────────────────────┐
│ VTS1 remediation    │  Oracle → TXT → clear column → import_csv
└─────────┬───────────┘
          │ re-run pre-check
          ▼
     All minimums OK?
          │ no → stop (insufficient data)
          ▼ yes
┌─────────────────────┐
│ Phase B: Prep       │  Alignment validations + clear old test data
└─────────┬───────────┘
          │ fail VTS1 validations?
          ▼
┌─────────────────────┐
│ VTS1 remediation    │  (same as above, then retry prep)
└─────────┬───────────┘
          ▼
     Prep complete → "Old test data has been removed"
```

### 5.2 Phase A — Pre-check (minimum counts)

Queries `countsPerColumn` from each VTS `get_page` API.

**VTS 1 — all must pass (count ≥ threshold)**

| Column | Minimum count |
|--------|----------------|
| P_BAN | 8,000 |
| P_SIM | 8,000 |
| P_IMEI_IP14128PUR | 8,000 |

**VTS 2 — all must pass**

| Column | Minimum count |
|--------|----------------|
| P_BAN_C | 1,000 |
| P_BAN_H | 1,000 |

**Outcome:** Per-column **Passed / Failed** with count. If any fail, flow attempts VTS1 remediation (below) then re-checks. If still failing, task stops.

### 5.3 Phase B — Data details & validations

After pre-check passes, prep re-reads counts and runs alignment rules.

**VTS 1 — data details displayed**

- P_BAN, P_SUBSCRIBER, P_SIM, P_IMEI_IP14128PUR

**VTS 1 — validation rules**

| Rule | Condition |
|------|-----------|
| P_BAN & P_SUBSCRIBER alignment | Counts must be **equal** |
| P_SIM minimum | Count ≥ **8,000** |
| P_IMEI_IP14128PUR minimum | Count ≥ **8,000** |

**VTS 2 — validation rules**

| Rule | Columns that must have **equal** counts |
|------|----------------------------------------|
| Current group | P_BAN_C, P_NPA_NXX_C, P_SUBSCRIBER_C, P_IMEI_C, P_SIM_C, P_ESN_0, P_ESN_1, P_IMSI_C |
| History group | P_BAN_H, P_SUBSCRIBER_H, P_OLD_IMEI, P_OLD_SIM, P_ESN_SEQ_NO, P_ESN_SEQ_NO1, P_IMSI |
| P_NEW_IMEI vs P_BAN_H | P_NEW_IMEI count ≥ P_BAN_H count |

If any validation fails, VTS1-specific failures trigger **Oracle remediation** (VTS2 remediation — planned for a later phase).

### 5.4 VTS 1 automated remediation (Oracle → TXT → VTS import)

When pre-check or prep fails for VTS1 columns, the portal:

1. Clears the target VTS1 column(s)
2. Runs the configured Oracle SQL
3. Writes a `.txt` file under `exports/vts1_remediate_<timestamp>/`
4. Imports into VTS1 via `import_csv`

| Trigger | Oracle purpose | TXT file | Header row |
|---------|----------------|----------|------------|
| P_BAN low or P_BAN ≠ P_SUBSCRIBER | BAN + subscriber extract (billing_account / subscriber / device / SOC filters) | P_BAN_P_SUBSCRIBER.txt | P_BAN,P_SUBSCRIBER |
| P_SIM < 8000 | SIM serial inventory query | P_SIM.txt | P_SIM |
| P_IMEI_IP14128PUR < 8000 | Dealer equipment IMEI extract | P_IMEI_IP14128PUR.txt | P_IMEI_IP14128PUR |

SQL defaults are editable via **Oracle Queries** (stored in `config/oracle_queries.json` after save).

### 5.5 Phase C — Remove old test data (on full prep success)

Clears legacy test columns via `clear_column`:

**VTS 2:** HUP_BAN, Resume_BAN  

**VTS 1:** NAC_BAN, PPC_BAN, TOR_BAN, AdSc_BAN, Cancel_BAN, Cancel_SUB_BAN, SmSw_BAN, BnAt_BAN, BYOD_BAN, AAL_BAN, VSY_BAN

**Completion message:** *Old test data has been removed.*

### 5.6 Supporting actions

| Action | Purpose |
|--------|---------|
| **Test Oracle DB** | Validates `.env` + `SELECT 1 FROM DUAL` |
| **Oracle Queries** | View/edit the three VTS1 remediation SQL scripts |

---

## 6. Task 2 — LG Disk Space Check

**Purpose:** Confirm adequate free disk on LG (load generator) machines before test execution.

**API:** `POST /api/tasks/lg-disk-check/run`

### 6.1 Servers checked

| IP | Machine name |
|----|----------------|
| 10.61.139.26 | VWRENBPTLRCAP01 |
| 10.61.139.27 | VWRENBPTLRGAP02 |
| 10.61.139.28 | VWRENBPTLRGAP03 |
| 10.61.139.29 | VWRENBPTLRGAP04 |
| 10.61.139.30 | VWRENBPTLRGAP05 |
| 10.61.139.31 | VWRENBPTLRGAP06 |
| 10.61.129.119 | VM0196PTLRG009 |

### 6.2 Checks performed

| Check | Method | Output |
|-------|--------|--------|
| C: drive total / free (GB) | Remote WMI (`Win32_LogicalDisk`) via DCOM | Table per server |
| D: drive total / free (GB) | Same | Table per server |
| Connectivity / WMI errors | Exception handling | Row marked **Error** with message |

Optional credentials: `LG_WMI_USER`, `LG_WMI_PASSWORD`, `LG_WMI_DOMAIN` in `.env`.

### 6.3 Pass / warn logic

| Condition | UI indication |
|-----------|----------------|
| Free space ≥ 50 GB | Green |
| Free space 15–49 GB | Yellow |
| Free space < 15 GB | Red |
| Any C: or D: free **< 20 GB** | Banner: critical warning |
| WMI failure | Error cells, no disk values |

Operators can open `\\<machine>\<C|D>$` from the UI for manual inspection.

---

## 7. Task 3 — Servers Log Space Check

**Purpose:** Monitor log filesystem usage on TUX, PRAPI, and APILINK hosts; warn before logs fill disks; auto-clean when usage is critical.

**API:** `POST /api/tasks/server-log-check/run`

### 7.1 Server groups

| Group | Hosts (examples) | Usage CGI |
|-------|------------------|-----------|
| **TUX** | VLRENBPTCOLAP01, VLRENBPTCOLAP02 | `fetchPetV21FsUsage` |
| **PRAPI** | VLRENBPTTUXAP01 … TUXAP10 | `fetchPetV21FsUsage` |
| **APILINK** | LDCPETAPICL01 … APICL08 | `fetchPetAPILinkFsUsage` |

Base URL: `NPE_SERVICES_CGI_BASE` (default `http://npe-services:8888/cgi-bin`).

### 7.2 Checks performed

| Step | Description |
|------|-------------|
| Parallel usage fetch | All hosts queried; `fsUsage` parsed as integer % |
| **Warning threshold** | Usage **> 60%** — logged and highlighted |
| **Critical threshold** | Usage **> 70%** — triggers automated clean |
| Fetch errors | Hosts that return HTTP/JSON errors listed separately |

### 7.3 Automated clean (usage > 70%)

For each critical host:

1. Call clean CGI (`cleanPetV21Logs` or `cleanPetAPILinkLogs`) with progressively shorter retention (15 days → 7 → 3.5 → …).
2. Re-fetch usage after each clean attempt.
3. Stop when host drops **below 60%**, or clean steps are exhausted.

Activity log and per-host status are shown in the task output.

---

## 8. Task 4 — Server Bounce

**Purpose:** Coordinate TUX server bounce through the official batch portal and track completion via ReqCentral / JIRA.

**Behaviour on RUN:** Task moves to **In Progress** (not auto-completed).

### 8.1 Operator steps

1. Portal opens guidance and link to **NPR batch portal**  
   `http://nprapw000379.rci.rogers.ca/batch/create`
2. Operator creates bounce request in NPR (manual).
3. Enter **JIRA issue key** (e.g. `RNER-197725`) in the portal.
4. Click **Check status** — backend fetches ReqCentral browse page (`REQCENTRAL_BROWSE_BASE`).

### 8.2 Status checks

| ReqCentral status | Task state |
|-------------------|------------|
| **Done** (or equivalent completed) | Task → **Completed** |
| **In Progress** | Task stays **In Progress** |
| Invalid key / fetch error | Message shown; operator retries |

### 8.3 Backend bounce probe (secondary)

`POST /api/tasks/server-bounce/run` can SSH to TUX COLAP servers and run configured bounce status command (`TUX_BOUNCE_COMMAND`). Used for optional status listing; primary Task 4 workflow is NPR + JIRA tracking.

MobaXterm may be launched automatically if installed (`MOBAXTERM_PATH`).

---

## 9. Task 5 — Server Health Check

**Purpose:** Confirm TUX OLN and TUX API (PRAPI) servers report **UP** before testing.

**API:** `POST /api/tasks/server-health-check/run`

### 9.1 Data source

- Fetches **PET1-V21** HTML from NPE CGI  
  Default: `http://npe-services:8888/cgi-bin/PET1-V21`
- Parsed server tables: **TUX OLN** (COLAP) and **TUX API** (TUXAP)

### 9.2 Checks performed

| Check | Pass condition |
|-------|----------------|
| HTTP reachability | PET1-V21 returns 200 |
| HTML parse | Server name + status rows extracted |
| Per-server status | Normalized to **up** / **down** / **unknown** |
| Overall health | **allUp** when every server is up |
| Warnings | Any server **down** listed in warnings |

Output: grouped tables (TUX OLN vs TUX API) with colour-coded status.

---

## 10. Task 6 — Trigger Sanity (placeholder)

**Purpose:** Reserved for post-prep sanity validation.

**Current state:** Simulated run only — no external system calls. To be replaced with real sanity checks when defined.

---

## 11. Recommended execution order

Typical PET pre-check sequence:

| Order | Activity |
|-------|----------|
| 1 | HUP Offer Cleanup email (+ JIRA BR per process) |
| 2 | CRM IGW Bounce email (+ JIRA BR per process) |
| 3 | **Task 1** — Data Loading |
| 4 | **Task 2** — LG Disk Space |
| 5 | **Task 3** — Servers Log Space |
| 6 | **Task 4** — Server Bounce (await JIRA Done) |
| 7 | **Task 5** — Server Health Check |
| 8 | **Task 6** — Trigger Sanity (when implemented) |

---

## 12. Deployment & Git workflow

| Machine | Action |
|---------|--------|
| Developer PC | Edit code → `git commit` → `git push` |
| VDI | `git pull` → ensure `.env` exists (`setup-env.bat`) → `python app.py` |

`.env` is **never** pushed to GitHub.

---

## 13. Future enhancements (documented gaps)

| Item | Status |
|------|--------|
| JIRA BR auto-create + attach to HUP / CRM emails | Planned |
| VTS2 Oracle remediation (validation failures) | Planned |
| Task 6 real sanity checks | Planned |
| Full JIRA REST integration (create issue from portal) | Planned |

---

## 14. Document history

| Version | Date | Notes |
|---------|------|-------|
| 1.0 | 2026-06-01 | Initial HLD from implemented portal behaviour |
