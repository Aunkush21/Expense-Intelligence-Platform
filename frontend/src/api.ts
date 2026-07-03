// Typed client for the Expense Intelligence backend.

export interface Account {
  id: number
  name: string
  institution: string | null
  account_type: string
  created_at: string
}

export interface Transaction {
  id: number
  account_id: number
  txn_date: string
  merchant: string
  description: string | null
  amount: number
  category: string
  category_source: string
  is_recurring: boolean
}

export interface SummaryStats {
  total_spend: number
  total_income: number
  net: number
  transaction_count: number
  top_category: string | null
  start_date: string | null
  end_date: string | null
}

export interface CategorySpend {
  category: string
  total: number
  transaction_count: number
}

export interface MonthlyTrend {
  month: string
  total_spend: number
  total_income: number
}

export interface Subscription {
  id: number
  merchant: string
  cadence: string
  average_amount: number
  occurrences: number
  last_seen: string
  next_expected: string | null
}

export interface Anomaly {
  id: number
  transaction_id: number
  reason_code: string
  detail: string | null
  detected_at: string
  txn_date: string
  merchant: string
  amount: number
  category: string
}

export interface IngestionResult {
  account_id: number
  rows_received: number
  inserted: number
  duplicates_skipped: number
  failed: number
  message: string
  schema_mapping: Record<string, string>
  schema_notes: string[]
  warnings: string[]
}

export interface PreviewRow {
  txn_date: string
  merchant: string
  amount: number
}

export interface SchemaPreview {
  usable: boolean
  detected_columns: string[]
  mapping: Record<string, string>
  notes: string[]
  warnings: string[]
  missing: string[]
  sample_rows: PreviewRow[]
}

// --- Cookie auth + authenticated fetch with silent refresh ------------------
//
// Tokens live in httpOnly cookies the browser sends automatically, so this
// client never reads or stores them. On a 401 we transparently try a single
// refresh and replay the request; if that fails, we hand off to the app to
// show the login screen.

export interface User {
  id: number
  email: string
  created_at: string
}

let onUnauthorized: () => void = () => {}
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn
}

async function tryRefresh(): Promise<boolean> {
  const res = await fetch('/api/auth/refresh', {
    method: 'POST',
    credentials: 'include',
  })
  return res.ok
}

async function authedFetch(
  url: string,
  options: RequestInit = {},
  retry = true,
): Promise<Response> {
  const res = await fetch(url, { ...options, credentials: 'include' })
  if (res.status === 401) {
    if (retry && (await tryRefresh())) {
      return authedFetch(url, options, false)
    }
    onUnauthorized()
    throw new Error('Your session has expired — please log in again.')
  }
  return res
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error(detail.detail || `Request failed (${res.status})`)
  }
  return res.json() as Promise<T>
}

export const api = {
  // Bootstrap check: returns the user if a valid session cookie exists.
  me: () => authedFetch('/api/auth/me').then(json<User>),

  register: (email: string, password: string) =>
    fetch('/api/auth/register', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    }).then(json<User>),

  login: (email: string, password: string) => {
    // The login endpoint uses the OAuth2 password form (username = email).
    const body = new URLSearchParams({ username: email, password })
    return fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    }).then(json<User>)
  },

  logout: () =>
    fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }),

  listAccounts: () => authedFetch('/api/accounts').then(json<Account[]>),

  createAccount: (name: string, institution: string) =>
    authedFetch('/api/accounts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, institution }),
    }).then(json<Account>),

  previewStatement: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return authedFetch('/api/statements/preview', {
      method: 'POST',
      body: form,
    }).then(json<SchemaPreview>)
  },

  uploadStatement: (accountId: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return authedFetch(`/api/accounts/${accountId}/statements`, {
      method: 'POST',
      body: form,
    }).then(json<IngestionResult>)
  },

  summary: (accountId: number) =>
    authedFetch(`/api/accounts/${accountId}/analytics/summary`).then(json<SummaryStats>),

  byCategory: (accountId: number) =>
    authedFetch(`/api/accounts/${accountId}/analytics/by-category`).then(
      json<CategorySpend[]>,
    ),

  trends: (accountId: number) =>
    authedFetch(`/api/accounts/${accountId}/analytics/trends`).then(json<MonthlyTrend[]>),

  subscriptions: (accountId: number) =>
    authedFetch(`/api/accounts/${accountId}/subscriptions`).then(json<Subscription[]>),

  anomalies: (accountId: number) =>
    authedFetch(`/api/accounts/${accountId}/anomalies`).then(json<Anomaly[]>),

  digestPreview: (accountId: number) =>
    authedFetch(`/api/accounts/${accountId}/digest/preview`).then(
      json<{ account_id: number; subject: string; body: string; html: string }>,
    ),

  sendDigest: (accountId: number) =>
    authedFetch(`/api/accounts/${accountId}/digest/send`, { method: 'POST' }).then(
      json<{ account_id: number; subject: string; delivery: string }>,
    ),

  schedulerStatus: () =>
    authedFetch('/api/automation/status').then(
      json<{
        running: boolean
        cadence: string
        next_run: string | null
        delivery_mode: string
      }>,
    ),

  transactions: (accountId: number) =>
    authedFetch(`/api/accounts/${accountId}/transactions?limit=200`).then(
      json<Transaction[]>,
    ),

  correctCategory: (txnId: number, category: string) =>
    authedFetch(`/api/transactions/${txnId}/category`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category }),
    }).then(json<Transaction>),

  clearData: (accountId: number) =>
    authedFetch(`/api/accounts/${accountId}/transactions`, { method: 'DELETE' }).then(
      json<{ account_id: number; deleted: number }>,
    ),
}
