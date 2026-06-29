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

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}))
    throw new Error(detail.detail || `Request failed (${res.status})`)
  }
  return res.json() as Promise<T>
}

export const api = {
  listAccounts: () => fetch('/api/accounts').then(json<Account[]>),

  createAccount: (name: string, institution: string) =>
    fetch('/api/accounts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, institution }),
    }).then(json<Account>),

  previewStatement: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return fetch('/api/statements/preview', {
      method: 'POST',
      body: form,
    }).then(json<SchemaPreview>)
  },

  uploadStatement: (accountId: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return fetch(`/api/accounts/${accountId}/statements`, {
      method: 'POST',
      body: form,
    }).then(json<IngestionResult>)
  },

  summary: (accountId: number) =>
    fetch(`/api/accounts/${accountId}/analytics/summary`).then(json<SummaryStats>),

  byCategory: (accountId: number) =>
    fetch(`/api/accounts/${accountId}/analytics/by-category`).then(
      json<CategorySpend[]>,
    ),

  trends: (accountId: number) =>
    fetch(`/api/accounts/${accountId}/analytics/trends`).then(json<MonthlyTrend[]>),

  transactions: (accountId: number) =>
    fetch(`/api/accounts/${accountId}/transactions?limit=200`).then(
      json<Transaction[]>,
    ),

  correctCategory: (txnId: number, category: string) =>
    fetch(`/api/transactions/${txnId}/category`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category }),
    }).then(json<Transaction>),

  clearData: (accountId: number) =>
    fetch(`/api/accounts/${accountId}/transactions`, { method: 'DELETE' }).then(
      json<{ account_id: number; deleted: number }>,
    ),
}
