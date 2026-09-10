# V21 PreChecks Automation Portal — High Level Design

**What is this?**  
A simple web portal that runs pre-test checks before V21 performance testing. Instead of jumping between tools, emails, and manual scripts, QE runs everything from one screen on the VDI.

**Who uses it?** QE Performance Engineering  
**How to run it?** `python app.py` → open the portal in a browser

---

## What the portal does (in one sentence)

It checks that test data, disks, logs, and servers are ready — and sends the right emails to DBA / NPE when manual work is needed.

---

## Email triggers (top of the page)

### CRM IGW Bounce Mail

**What you want to happen**

1. A JIRA BR is raised for the reboot.
2. An email goes to NPE Support with the BR attached.
3. NPE reboots CRM IGW Domain 1 servers.

**What the portal does today**

- Click the button → email is sent to **RCI V21 NPE Support** (QE is CC’d).
- Email asks NPE to reboot CRM IGW Domain 1.
- **JIRA BR automation is not built yet** — same as HUP, BR is manual for now.

---

## Task 1 — Data Loading

**Goal:** Make sure VTS test data is complete, correct, and old junk columns are cleared.

**Systems involved**

- **VTS 1** — port 2244  
- **VTS 2** — port 2245  
- **Oracle** — when VTS 1 needs fresh data  

---

### Step 1 — Pre-check (enough data?)

The portal reads row counts from VTS and checks minimums.

**VTS 1 — need at least:**

| Column | Minimum rows |
|--------|----------------|
| P_BAN | 8,000 |
| P_SIM | 8,000 |
| P_IMEI_IP14128PUR | 8,000 |

**VTS 2 — need at least:**

| Column | Minimum rows |
|--------|----------------|
| P_BAN_C | 1,000 |
| P_BAN_H | 1,000 |

Each column shows **Passed** or **Failed** with the actual count.

If VTS 1 fails → portal tries to fix it automatically (see Step 3), then checks again.

---

### Step 2 — Validations (data aligned?)

If pre-check passes, deeper rules run.

**VTS 1**

- P_BAN and P_SUBSCRIBER must have the **same** count.
- P_SIM must still be ≥ 8,000.
- P_IMEI_IP14128PUR must still be ≥ 8,000.

**VTS 2**

- **Current columns** must all match each other:  
  P_BAN_C, P_NPA_NXX_C, P_SUBSCRIBER_C, P_IMEI_C, P_SIM_C, P_ESN_0, P_ESN_1, P_IMSI_C
- **History columns** must all match each other:  
  P_BAN_H, P_SUBSCRIBER_H, P_OLD_IMEI, P_OLD_SIM, P_ESN_SEQ_NO, P_ESN_SEQ_NO1, P_IMSI
- P_NEW_IMEI count must be ≥ P_BAN_H count.

If VTS 1 validation fails → auto-fix from Oracle (Step 3), then retry.

---

### Step 3 — Auto-fix VTS 1 (Oracle → file → import)

When VTS 1 is short or misaligned, the portal:

1. Connects to Oracle  
2. Runs the right query  
3. Saves a `.txt` file  
4. Clears the old VTS column  
5. Imports the file into VTS 1  

| Problem | What gets loaded |
|---------|------------------|
| P_BAN too low, or P_BAN ≠ P_SUBSCRIBER | P_BAN + P_SUBSCRIBER from billing/subscriber query |
| P_SIM too low | SIM serial numbers |
| P_IMEI_IP14128PUR too low | IMEI numbers |

You can view or edit these SQL queries using the **Oracle Queries** button.

---

### Step 4 — Clean up old test columns

When everything passes, old test columns are cleared.

- **VTS 2:** HUP_BAN, Resume_BAN, SmSw_BAN  
- **VTS 1:** NAC_BAN, PPC_BAN, TOR_BAN, AdSc_BAN, Cancel_BAN, Cancel_SUB_BAN, BnAt_BAN, BYOD_BAN, AAL_BAN, VSY_BAN 

**Done message:** *Old test data has been removed.*

---

### Extra buttons on Task 1

| Button | What it does |
|--------|----------------|
| **Test Oracle DB** | Checks that Oracle connection works |
| **Oracle Queries** | View / edit the 3 SQL scripts used for auto-fix |

---

## Task 2 — HUP Offer Cleanup

1. Read the current `HUP_OFFER` count from NPE services.
2. If the count is zero, complete without starting a cleanup.
3. Otherwise, start the cleanup and capture its `pid` and `jobId`.
4. Poll the job-status API every five seconds until its status is `completed`.
5. Show deleted and current record counts; mark the task complete when the current count is zero.

---

## Task 3 — LG Disk Space Check

**Goal:** Make sure load generator (LG) machines have enough disk space.

**What it checks**

- 7 LG servers (VWRENBPTLRCAP01, GAP02–GAP06, VM0196PTLRG009)
- Free space on **C:** and **D:** drives (in GB)

**How results are shown**

| Free space | Meaning |
|------------|---------|
| Green (50 GB+) | Good |
| Yellow (15–49 GB) | Watch it |
| Red (under 15 GB) | Low |
| Warning banner | Any drive under **20 GB** free |

You can click a drive in the table to open the server share for manual check.

---

## Task 4 — Servers Log Space Check

**Goal:** Make sure log folders on app servers are not filling up the disk.

**Servers checked**

- **TUX** (2 servers)  
- **PRAPI** (10 servers)  
- **APILINK** (8 servers)  

**Rules**

| Log disk usage | What happens |
|----------------|--------------|
| Over **60%** | Warning shown |
| Over **70%** | Portal **automatically cleans old logs** |
| After clean | Re-checks usage; stops when below 60% |

Full activity log is shown in the task output.

---

## Task 5 — Server Bounce

**Goal:** Bounce TUX servers through the proper change process and wait until JIRA says it’s done.

**This task stays “In Progress” until you finish manually.**

1. Click **RUN** — portal shows a link to the **NPR batch portal** to create the bounce request.  
2. Submit your bounce in NPR (outside the portal).  
3. Enter your **JIRA ticket number** (e.g. RNER-197725) in the portal.  
4. Click **Check status** — portal reads ReqCentral and tells you if the ticket is Done or still In Progress.  
5. When JIRA is **Done** → task marks **Completed**.

---

## Task 6 — Server Health Check

**Goal:** Confirm TUX servers are **UP** before testing.

**What it does**

- Pulls the **PET1-V21** health page from NPE services  
- Lists **TUX OLN** and **TUX API** servers with Up / Down status  
- Warns if any server is down  

---

## Task 7 — Trigger Sanity

**Goal:** Run sanity checks after everything else (placeholder for now).

**Today:** Simulated only — real checks will be added later.

---

## Suggested order to run everything

1. CRM IGW Bounce (email + JIRA BR)
2. Task 1 — Data Loading
3. Task 2 — HUP Offer Cleanup
4. Task 3 — LG Disk Space
5. Task 4 — Servers Log Space
6. Task 5 — Server Bounce (wait for JIRA Done)
7. Task 6 — Server Health Check
8. Task 7 — Trigger Sanity (when ready)

---

## Setup notes (VDI)

- Passwords and URLs live in a **`.env`** file on the VDI — this file is **not** on GitHub.  
- After `git pull`, run **`setup-env.bat`** once if `.env` is missing.  
- Start the portal with **`python app.py`**.

---

## Coming later

- Auto-create JIRA BR and attach to HUP / CRM emails  
- Auto-fix for VTS 2 validation failures  
- Real sanity checks in Task 6  

---

*Version 1.1 — simplified for readability*
