# Dhan Historical Data Downloader Portal — Lovable AI Build Prompt

## Project Overview

Build a modern, web-based portal called **"Dhan Historical Data Downloader"** that allows traders to download historical market data from Dhan brokerage's V2 API. The portal must support three asset classes through a tabbed interface: **Expired Options**, **Index Futures**, and **Equity (Bulk)**.

The portal is exclusively designed to work with **Dhan brokerage** (https://dhan.co). The main page must prominently display this fact along with a highlighted call-to-action button to open a free Dhan account using the affiliate referral link.

---

## Tech Stack Recommendation

- **Frontend**: React + TypeScript + Tailwind CSS + shadcn/ui components
- **Backend**: Node.js (Express) or Python (FastAPI) as a backend proxy for Dhan API calls
- **State management**: React Context or Zustand for download progress tracking
- **Real-time updates**: Server-Sent Events (SSE) or polling every 700ms for progress updates
- **File handling**: CSV parsing on client side using PapaParse library
- **Storage**: Browser-based downloads (zip the output folder structure for user download)

---

## Brand and Visual Design

### Color Palette (Dark Theme)
- Background: `#0f1419` (deep dark blue-black)
- Panel background: `#161d27`
- Border: `#243044`
- Primary text: `#d7dee8`
- Muted text: `#7d8aa0`
- Accent blue (primary action): `#3b82f6`
- Success green: `#10b981`
- Error red: `#ef4444`
- Warning amber: `#f59e0b`

### Typography
- Font: System font stack (`-apple-system, Segoe UI, Roboto, sans-serif`)
- Base size: 14px
- Headings: 16px to 24px, weight 600

### Layout
- Header bar at top with app title and Dhan account CTA
- Tabbed interface below (3 tabs: Expired Options, Futures, Equity)
- Form on the left side of each tab
- Progress panel on the right side (or below on mobile)
- Documentation accessible via a "How to Use" button in the header

---

## Main Page Layout

### Header Section (Always Visible)
1. **Left side**: Logo and title "Dhan Historical Data Downloader"
2. **Center**: Tagline reading "Download historical equity, futures, and options data directly from Dhan API"
3. **Right side**: Two prominent buttons:
   - **"Open Free Dhan Account"** (primary green/blue gradient button, opens in new tab to: `https://invite.dhan.co/?join=KIRU`)
   - **"How to Use"** (secondary button, opens documentation modal or page)

### Critical Notice Banner (Below Header)
A highlighted banner stating:
> "This portal works exclusively with Dhan brokerage. You need an active Dhan account and API access token to use this tool. Don't have a Dhan account yet? [Open Free Account →]"

Make the "Open Free Account" link button-styled and visually prominent.

---

## Tab 1: Expired Options Downloader

### Purpose
Download historical price, volume, and open interest data for expired index options (NIFTY and SENSEX) using Dhan's `/charts/rollingoption` API. Output is one CSV file per trading date with all strikes and option types combined.

### Form Fields

1. **Index** (dropdown)
   - Options: NIFTY (securityId: 13, exchangeSegment: NSE_FNO), SENSEX (securityId: 51, exchangeSegment: BSE_FNO)
   - Default: NIFTY

2. **Client ID** (text input, optional)
   - Used only for logging purposes
   - Placeholder: "Your Dhan client ID (optional)"

3. **Access Token** (password input, required)
   - Dhan API access token
   - Show/hide toggle
   - Placeholder: "Paste your Dhan API access token"
   - Helper text: "Get your access token from Dhan dashboard, API section"

4. **From Date** (date picker, required)
   - Default: 30 days ago

5. **To Date** (date picker, required)
   - Default: today

6. **Time Frame / Interval** (dropdown, required)
   - Valid values: 1 min, 5 min, 15 min, 25 min, 60 min
   - Default: 5 min

7. **Expiry Type** (dropdown)
   - Options: WEEK (weekly expiry), MONTH (monthly expiry)
   - Default: WEEK

8. **Expiry Code** (number input)
   - Range: 0 to 5
   - Default: 0 (current week or current month)
   - Helper text: "0 = current expiry, 1 = next expiry, etc."

9. **Strike Range** (number input, 0 to 10)
   - Default: 3
   - Helper text: "ATM ± N strikes (3 means ATM-3 to ATM+3, total 7 strikes)"
   - Build strike list as: ATM-N, ATM-(N-1), ..., ATM-1, ATM, ATM+1, ..., ATM+N

10. **Option Type** (dropdown)
    - Options: CALL, PUT, BOTH
    - Default: BOTH

11. **Output Folder Path** (text input)
    - Default: `~/Downloads/dhan_options`
    - In browser context, this becomes the zip file name prefix

12. **Start Download** button (primary CTA)
13. **Cancel** button (visible only during download)

### API Endpoint Logic

Backend should call: `POST https://api.dhan.co/v2/charts/rollingoption`

Headers: `{ "access-token": "<user_token>", "Content-Type": "application/json" }`

Payload structure:
```json
{
  "exchangeSegment": "NSE_FNO or BSE_FNO",
  "interval": "5",
  "securityId": "13 or 51",
  "instrument": "OPTIDX",
  "expiryFlag": "WEEK or MONTH",
  "expiryCode": 0,
  "strike": "ATM or ATM+1 or ATM-1 etc",
  "drvOptionType": "CALL or PUT",
  "requiredData": ["open","high","low","close","volume","oi","iv","strike","spot"],
  "fromDate": "YYYY-MM-DD",
  "toDate": "YYYY-MM-DD"
}
```

### Important Implementation Details

- Chunk the date range into 29-day chunks since Dhan API has limits
- The `toDate` in the API call must be sent as `actual_to_date + 1 day` since Dhan treats `toDate` as non-inclusive
- Loop through all combinations: each strike × each option type × each date chunk
- Convert UTC timestamps to IST (add 5 hours 30 minutes)
- Group rows by date and write one CSV per date with all strikes combined
- CSV columns: `datetime, strike_label, option_type, open, high, low, close, volume, oi, iv, strike_price, spot`
- Sort rows by datetime, then strike position, then option type
- Folder structure: `{index}/{expiryFlag}_E{expiryCode}_{interval}m/{from}_to_{to}/`
- File naming: `{INDEX}_{YYYY-MM-DD}_{interval}m.csv`

---

## Tab 2: Index Futures Downloader

### Purpose
Download NIFTY index futures historical data with automatic contract rollover. The portal must read a Dhan scrip master CSV file (`api-scrip-master-detailed.csv`) to identify available futures contracts and assign the correct contract to each date based on expiry rolling.

### Form Fields

1. **Client ID** (text input, optional)

2. **Access Token** (password input, required)

3. **From Date** (date picker, required, default 30 days ago)

4. **To Date** (date picker, required, default today)

5. **Time Frame** (dropdown)
   - Options: Daily (EOD), 1 min, 5 min, 15 min, 25 min, 60 min
   - Default: Daily
   - Note: Daily uses `/charts/historical`, intraday uses `/charts/intraday`

6. **Output Folder Path** (text input)
   - Default: `~/Downloads/dhan_futures`

7. **Scrip Master CSV Upload** (file input)
   - User uploads `api-scrip-master-detailed.csv` from Dhan
   - Helper text with download link: "Download the latest scrip master from Dhan API documentation"
   - Show a preview of loaded NIFTY futures contracts after upload

8. **Available Contracts Display** (read-only panel)
   - Shows list of detected NIFTY futures contracts with: Expiry date, Display name, Security ID
   - Updates after CSV upload

9. **Start Download** button
10. **Cancel** button

### API Endpoint Logic

**For Daily**: `POST https://api.dhan.co/v2/charts/historical`

**For Intraday**: `POST https://api.dhan.co/v2/charts/intraday`

Payload structure:
```json
{
  "securityId": "<contract_security_id>",
  "exchangeSegment": "NSE_FNO",
  "instrument": "FUTIDX",
  "oi": true,
  "fromDate": "YYYY-MM-DD or YYYY-MM-DD 09:15:00",
  "toDate": "YYYY-MM-DD or YYYY-MM-DD 15:30:00",
  "interval": "5"
}
```
(interval only included for intraday calls)

### Scrip Master Parsing Logic

Parse the uploaded `api-scrip-master-detailed.csv` and filter rows where:
- `INSTRUMENT` equals `FUTIDX`
- `UNDERLYING_SECURITY_ID` equals `26000` (NIFTY underlying ID)

Extract for each row:
- `SECURITY_ID`
- `DISPLAY_NAME`
- `SM_EXPIRY_DATE` (parse first 10 characters as YYYY-MM-DD)

Sort contracts by expiry date ascending.

### Contract Rollover Logic

For each calendar date in the user's selected range:
1. Find the contract with the earliest expiry date that is greater than or equal to that calendar date
2. That contract is assigned to that date
3. Group dates by assigned contract

If no contract covers some dates, show a warning listing the uncovered months and suggest refreshing the scrip master CSV.

### Chunking

- For daily interval: max 365 days per API call
- For intraday: max 90 days per API call
- Split contiguous date assignments into chunks of allowed size

### Output

- Folder: `NIFTY_FUT/{D or interval+m}/{from}_to_{to}/`
- One CSV per date with columns: `datetime, contract_expiry, contract_name, security_id, open, high, low, close, volume, oi`
- File naming: `NIFTY_FUT_{YYYY-MM-DD}_{daily or interval+m}.csv`

---

## Tab 3: Equity Bulk Downloader

### Purpose
Download historical data for multiple equity stocks in bulk based on a CSV file containing security IDs and symbols. Output is one CSV per stock.

### Form Fields

1. **Client ID** (text input, optional)

2. **Access Token** (password input, required)

3. **Symbols CSV Upload** (file input, required)
   - Upload a CSV with columns: `SECURITY_ID` (required), `UNDERLYING_SYMBOL` (optional)
   - Show parsed preview: count of symbols and first 200 listed
   - Accept column name aliases (case-insensitive):
     - For Security ID: `SECURITY_ID`, `SECURITYID`, `SEC_ID`, `SECID`
     - For Symbol: `UNDERLYING_SYMBOL`, `SYMBOL`, `TRADING_SYMBOL`, `TICKER`

4. **From Date** (date picker, required, default 30 days ago)

5. **To Date** (date picker, required, default today)

6. **Time Frame** (dropdown)
   - Options: Daily (EOD), 1 min, 5 min, 15 min, 25 min, 60 min
   - Default: 5 min

7. **Output Folder Path** (text input)
   - Default: `~/Downloads/dhan_equity`

8. **Start Download** button
9. **Cancel** button

### API Endpoint Logic

Same as futures, but with:
- `exchangeSegment`: `NSE_EQ`
- `instrument`: `EQUITY`
- `oi`: `true`

### Chunking
- Daily: 365 days per chunk
- Intraday: 90 days per chunk

### Output

- Folder: `equity/{daily or interval+m}/{from}_to_{to}/`
- One CSV per symbol with columns: `datetime, symbol, security_id, open, high, low, close, volume, oi`
- File naming: `{SANITIZED_SYMBOL}_{SECURITY_ID}_{daily or interval+m}.csv` (replace non-alphanumeric chars with underscore)

---

## Progress Panel (Shared Across All Tabs)

A right-side panel that shows real-time download status. Always visible.

### Components

1. **Progress Bar**: Horizontal bar showing percentage complete (0 to 100)

2. **Stats Row**:
   - Percentage: e.g., "45%"
   - Count: e.g., "9 / 20" (items done out of total)

3. **Status Message**: Currently processing item, e.g., "ATM+1 CALL" or "RELIANCE (secId 2885)" or "idle"

4. **Files Written Section**: Scrollable list showing each CSV file as it gets written. Each entry shows the relative path.

5. **Live Logs**: Scrollable monospace text area with timestamped log entries. Auto-scroll to bottom on new entries. Keep last 500 lines max.

6. **Error List**: Highlighted in red, showing any API errors encountered.

7. **Download All as ZIP** button (appears after completion): Bundle all generated CSVs into a single ZIP file for user download.

### Cancel Functionality

A red "Cancel" button is shown only when a download is running. Clicking it stops the download gracefully after the current API call completes.

---

## How to Use Documentation Page

Create a dedicated documentation page or modal accessible via the "How to Use" button in the header. The documentation must be comprehensive and beginner-friendly.

### Documentation Structure

#### Section 1: Getting Started

**What is this portal?**

This portal is a free tool that lets you download historical market data from Dhan brokerage in CSV format. You can download:
- Expired options data (NIFTY, SENSEX) with strikes, OI, IV, volume
- Index futures data (NIFTY) with automatic contract rollover
- Equity historical data in bulk for multiple stocks

**Why use this?**

If you build trading strategies, backtest systems, or analyze markets, you need clean historical data. Dhan provides this data through their V2 API, and this portal makes it easy to download in a structured format without writing code.

**Prerequisites**

1. An active Dhan trading account (free to open)
2. Dhan API access enabled
3. Your Dhan access token

If you do not have a Dhan account, click the "Open Free Dhan Account" button on the main page to create one. Use referral link: https://invite.dhan.co/?join=KIRU

#### Section 2: How to Get Your Dhan Access Token

Step-by-step guide:
1. Log in to your Dhan account at web.dhan.co
2. Navigate to the API section in your profile menu
3. Generate a new access token (valid for the day, typically expires daily)
4. Copy the token and paste it into the "Access Token" field in this portal
5. Keep your token secure, never share it publicly

#### Section 3: Tab 1 — Expired Options Tutorial

Detailed walkthrough with screenshots:

1. **Choose Index**: Select NIFTY or SENSEX based on which expired options you want.

2. **Enter Credentials**: Paste your Dhan access token. Client ID is optional.

3. **Set Date Range**: Pick your "From Date" and "To Date". The portal automatically splits this into 29-day chunks for API compatibility.

4. **Choose Interval**: 
   - 1 min: highest resolution, large file size
   - 5 min: good for most strategies
   - 15 min, 25 min, 60 min: lower resolution, smaller files

5. **Pick Expiry Type**:
   - WEEK: Weekly options expiry
   - MONTH: Monthly options expiry

6. **Set Expiry Code**: 
   - 0 means the nearest expiry from the date
   - 1 means the next expiry, 2 means the one after, etc.

7. **Strike Range**: How many strikes around ATM to download.
   - 0: Only ATM strike
   - 3: ATM-3, ATM-2, ATM-1, ATM, ATM+1, ATM+2, ATM+3 (7 strikes total)
   - 5: 11 strikes total

8. **Option Type**: CALL only, PUT only, or BOTH

9. **Click "Start Download"**. Watch the progress panel on the right.

10. **Output Files**: One CSV per trading date. Each CSV contains all selected strikes for both CALL and PUT (if BOTH selected), with OHLC, volume, open interest, implied volatility, strike price, and spot price.

**Column Glossary**:
- `datetime`: Timestamp in IST
- `strike_label`: ATM, ATM+1, ATM-2, etc.
- `option_type`: CALL or PUT
- `open, high, low, close`: OHLC prices
- `volume`: Traded volume in that interval
- `oi`: Open interest
- `iv`: Implied volatility
- `strike_price`: Actual strike price (in points)
- `spot`: Underlying spot price

#### Section 4: Tab 2 — Index Futures Tutorial

1. **Download Scrip Master**: Go to Dhan API docs and download `api-scrip-master-detailed.csv`. This file contains all instrument details including futures contracts and their expiry dates.

2. **Upload the CSV**: Use the file picker to upload the scrip master. The portal will automatically detect available NIFTY futures contracts and list them.

3. **Set Date Range**: From and To dates.

4. **Choose Time Frame**: Daily for end-of-day, or 1/5/15/25/60 min for intraday.

5. **Click "Start Download"**.

**How Contract Rollover Works**:

For each calendar date in your range, the portal finds the futures contract whose expiry is the earliest date that's still on or after that date. This gives you a continuous time series using the "front month" contract logic. The output CSV includes columns showing which contract each row came from.

**Column Glossary**:
- `datetime`: Date or timestamp in IST
- `contract_expiry`: Expiry date of the contract used for this row
- `contract_name`: Display name like "NIFTY 28 NOV"
- `security_id`: Dhan security ID
- `open, high, low, close, volume, oi`: Standard OHLC + volume + open interest

#### Section 5: Tab 3 — Equity Bulk Download Tutorial

1. **Prepare Your Symbols CSV**: Create a CSV with at least a `SECURITY_ID` column. Optionally include `UNDERLYING_SYMBOL` for nicer file names. Example:

```csv
SECURITY_ID,UNDERLYING_SYMBOL
2885,RELIANCE
1333,HDFCBANK
11536,TCS
```

You can find security IDs in the Dhan `api-scrip-master-detailed.csv` (filter by `INSTRUMENT = EQUITY` and `EXCHANGE_SEGMENT = NSE_EQ`).

2. **Upload the CSV**: The portal shows you a preview of detected symbols.

3. **Set Date Range and Time Frame**.

4. **Click "Start Download"**. The portal downloads each symbol sequentially. For 100 symbols at 5 min interval for 1 year, expect roughly 10 to 15 minutes.

**Output**: One CSV per symbol with full date range. Filename format: `RELIANCE_2885_5m.csv`.

#### Section 6: Understanding Dhan API Limits

- Intraday calls are limited to 90 days per request. The portal handles chunking automatically.
- Daily calls allow up to 365 days per request.
- Rate limit: Dhan limits API calls per second. The portal makes sequential calls to stay within limits. If you see rate limit errors, retry after a few seconds.
- Access tokens expire daily. You will need a fresh token each trading day.

#### Section 7: Troubleshooting

**"Access token is required" error**: Make sure you pasted the access token correctly. Tokens are case-sensitive.

**"http 401" or "unauthorized" error**: Your access token may have expired or is invalid. Generate a new one from your Dhan dashboard.

**"http 429" or rate limit error**: Too many requests in a short time. Wait 30 seconds and try again.

**"No contract covers these dates" warning (futures tab)**: Your scrip master CSV is too old. Download a fresh `api-scrip-master-detailed.csv` from Dhan that covers the date range you want.

**Empty CSVs or zero rows**: Check that your date range falls on trading days, the expiry code is valid, and the strike range you chose actually had traded data.

**Download is slow**: Each API call takes 1 to 3 seconds. Large ranges with many strikes and option types create many API calls. For 30 days of NIFTY weekly options with 7 strikes both CE and PE at 5 min, expect roughly 5 to 10 minutes.

#### Section 8: API Reference Links

- Dhan API v2 Documentation: https://dhanhq.co/docs/v2/
- Expired Options API: https://dhanhq.co/docs/v2/expired-options-data/
- Historical Data API: https://dhanhq.co/docs/v2/historical-data/
- Dhan Account Opening: https://invite.dhan.co/?join=KIRU

#### Section 9: Privacy and Security

- Your access token is sent only to the Dhan API, never stored on this portal's servers.
- All downloads happen on your device (or your browser session).
- The portal is open source and you can self-host it.
- Tokens are masked in the UI and not logged.

---

## Functional Requirements Summary

### Critical Features

1. Three-tab interface (Options, Futures, Equity) with shared progress panel
2. Dhan API authentication using user-supplied access tokens
3. Date range chunking based on Dhan API limits (29 days for options, 90 for intraday, 365 for daily)
4. CSV parsing for both symbols upload (equity) and scrip master (futures)
5. Automatic NIFTY futures contract rollover logic
6. ATM-relative strike notation parsing (ATM, ATM+N, ATM-N)
7. UTC to IST timezone conversion for all timestamps
8. Real-time progress tracking with progress bar, status text, and live logs
9. Cancel running downloads gracefully
10. Bundle output CSVs as ZIP for browser download
11. Prominent Dhan account opening CTA with referral link
12. Comprehensive How to Use documentation

### Error Handling

- Validate all form inputs before submission
- Show clear error messages for invalid tokens, expired sessions, network failures
- Display Dhan API error responses verbatim in the logs
- Prevent two simultaneous downloads (lock during run)
- Graceful cancellation that finishes current API call

### UX Polish

- Show/hide password toggle for access token field
- Date pickers default to a sensible 30-day range
- Disable Start button while a download is running
- Auto-scroll log panel to bottom on new entries
- Show file count and total size after completion
- Toast notifications for major events (started, completed, cancelled, error)
- Keyboard shortcut: Esc to cancel running download
- Responsive layout: side-by-side on desktop, stacked on mobile

### Performance

- Stream progress updates without blocking UI
- Polling interval of 700ms for progress
- Limit log buffer to last 500 entries
- Limit symbol preview to first 200 entries

---

## Backend Architecture Notes

Since Lovable AI primarily generates frontend, you have two implementation options:

### Option A: Pure Frontend (Recommended for Lovable)

Make Dhan API calls directly from the browser using `fetch()`. Bundle CSVs in browser using libraries like `jszip` and trigger downloads via blob URLs. 

Pros: No backend needed, deployable as static site.
Cons: CORS might be an issue if Dhan API does not allow browser origin. Test with Dhan first.

If CORS blocks direct calls, use a lightweight serverless proxy function (Vercel/Netlify edge function) that simply forwards requests to Dhan with the user's token in the header.

### Option B: With Backend Proxy

Build a small backend (FastAPI or Express) that proxies Dhan API calls. The frontend talks to your backend, the backend talks to Dhan. Use Server-Sent Events (SSE) for real-time progress streaming.

For Lovable, start with Option A and add a serverless proxy only if needed.

---

## Sample Code Snippets to Embed in the Prompt

### Strike List Builder Logic

```javascript
function buildStrikeList(n) {
  n = Math.max(0, Math.min(10, parseInt(n)));
  const strikes = [];
  for (let i = -n; i < 0; i++) strikes.push(`ATM${i}`);
  strikes.push("ATM");
  for (let i = 1; i <= n; i++) strikes.push(`ATM+${i}`);
  return strikes;
}
```

### Date Chunker

```javascript
function chunkDates(fromDate, toDate, days = 29) {
  const out = [];
  let cur = new Date(fromDate);
  const end = new Date(toDate);
  while (cur <= end) {
    const nxt = new Date(cur);
    nxt.setDate(nxt.getDate() + days);
    const chunkEnd = nxt > end ? end : nxt;
    out.push([cur.toISOString().slice(0,10), chunkEnd.toISOString().slice(0,10)]);
    cur = new Date(chunkEnd);
    cur.setDate(cur.getDate() + 1);
  }
  return out;
}
```

### UTC to IST Conversion

```javascript
function utcToIst(unixTs) {
  const istMs = (unixTs * 1000) + (5.5 * 60 * 60 * 1000);
  return new Date(istMs).toISOString().replace('T', ' ').slice(0, 19);
}
```

---

## Final Deliverables Expected from Lovable

1. A polished, production-ready React app with the three-tab interface
2. Working Dhan API integration for all three tabs
3. Real-time progress tracking with logs and file list
4. ZIP download of generated CSVs at the end
5. Embedded How to Use documentation accessible from the header
6. Prominent Dhan account opening CTA with the referral link `https://invite.dhan.co/?join=KIRU`
7. Dark theme matching the colors specified above
8. Mobile-responsive layout
9. Form validation and clear error messages
10. Cancel running downloads gracefully

---

## Important Reminders for Lovable

- The portal works **only** with Dhan brokerage. Mention this clearly on the main page.
- The Dhan referral link `https://invite.dhan.co/?join=KIRU` must be used wherever you link to Dhan account opening.
- Access tokens are sensitive. Mask them in the UI and never log them.
- Default date range is last 30 days. Default interval is 5 min for options/equity, Daily for futures.
- The output structure (folder hierarchy and CSV format) should match the structure described above so users can plug the data into existing backtesting code.
- Documentation should be visible from every tab via a "How to Use" button in the header.
- Make the "Open Free Dhan Account" button visually prominent (large, contrasting color, possibly with an icon).
