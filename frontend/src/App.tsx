import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  api,
  setUnauthorizedHandler,
  type Account,
  type Anomaly,
  type CategorySpend,
  type MonthlyTrend,
  type SchemaPreview,
  type Subscription,
  type SummaryStats,
  type Transaction,
} from './api'
import AuthScreen from './AuthScreen'
import './App.css'

const ROLE_LABEL: Record<string, string> = {
  date: 'Date',
  merchant: 'Merchant / description',
  amount: 'Amount',
  direction: 'Debit/credit indicator',
  debit: 'Debit (money out)',
  credit: 'Credit (money in)',
}

const CATEGORIES = [
  'Groceries', 'Dining', 'Transport', 'Shopping', 'Entertainment',
  'Subscriptions', 'Utilities', 'Health', 'Travel', 'Income',
  'Transfers', 'Fees', 'Uncategorized',
]

const COLORS = [
  '#14b8a6', '#0ea5e9', '#a78bfa', '#f472b6', '#fbbf24',
  '#34d399', '#fb923c', '#60a5fa', '#f87171', '#c084fc',
  '#2dd4bf', '#94a3b8', '#475569',
]

const money = (n: number) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD' })

// Recharts tooltip formatter receives a loosely-typed value; coerce to number.
const moneyTooltip = (v: unknown) => money(Number(v))

export default function App() {
  // null = still checking the session cookie; then true/false.
  const [authed, setAuthed] = useState<boolean | null>(null)
  const [account, setAccount] = useState<Account | null>(null)
  const [summary, setSummary] = useState<SummaryStats | null>(null)
  const [byCategory, setByCategory] = useState<CategorySpend[]>([])
  const [trends, setTrends] = useState<MonthlyTrend[]>([])
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([])
  const [anomalies, setAnomalies] = useState<Anomaly[]>([])
  const [txns, setTxns] = useState<Transaction[]>([])
  const [toast, setToast] = useState<{ msg: string; err?: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<SchemaPreview | null>(null)
  const [pendingFile, setPendingFile] = useState<File | null>(null)
  const [digest, setDigest] = useState<{ subject: string; body: string } | null>(null)
  const [scheduler, setScheduler] = useState<{
    cadence: string
    next_run: string | null
    delivery_mode: string
  } | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const flash = (msg: string, err = false) => {
    setToast({ msg, err })
    setTimeout(() => setToast(null), 3200)
  }

  const loadAnalytics = useCallback(async (accountId: number) => {
    const [s, c, t, subs, anom, tx] = await Promise.all([
      api.summary(accountId),
      api.byCategory(accountId),
      api.trends(accountId),
      api.subscriptions(accountId),
      api.anomalies(accountId),
      api.transactions(accountId),
    ])
    setSummary(s)
    setByCategory(c)
    setTrends(t)
    setSubscriptions(subs)
    setAnomalies(anom)
    setTxns(tx)
  }, [])

  // Route 401s (expired/invalid token) back to the login screen, and check for
  // an existing session cookie on first load.
  useEffect(() => {
    setUnauthorizedHandler(() => setAuthed(false))
    api
      .me()
      .then(() => setAuthed(true))
      .catch(() => setAuthed(false))
  }, [])

  // Once authenticated, reuse the user's account or create their first one.
  useEffect(() => {
    if (!authed) return
    ;(async () => {
      try {
        const accounts = await api.listAccounts()
        const acct =
          accounts[0] ?? (await api.createAccount('Everyday Checking', 'Demo Bank'))
        setAccount(acct)
        await loadAnalytics(acct.id)
      } catch (e) {
        flash((e as Error).message, true)
      }
    })()
  }, [authed, loadAnalytics])

  const onLogout = async () => {
    try {
      await api.logout()
    } finally {
      setAccount(null)
      setSummary(null)
      setTxns([])
      setSubscriptions([])
      setAnomalies([])
      setAuthed(false)
    }
  }

  // Step 1: infer how the file's columns map before importing anything.
  const onFileSelected = async (file: File) => {
    setBusy(true)
    try {
      const p = await api.previewStatement(file)
      setPreview(p)
      setPendingFile(file)
    } catch (e) {
      flash((e as Error).message, true)
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  // Step 2: confirm the import.
  const confirmImport = async () => {
    if (!account || !pendingFile) return
    setBusy(true)
    try {
      const res = await api.uploadStatement(account.id, pendingFile)
      flash(`${res.inserted} added · ${res.duplicates_skipped} duplicates skipped`)
      await loadAnalytics(account.id)
    } catch (e) {
      flash((e as Error).message, true)
    } finally {
      setBusy(false)
      setPreview(null)
      setPendingFile(null)
    }
  }

  const cancelImport = () => {
    setPreview(null)
    setPendingFile(null)
  }

  const onPreviewDigest = async () => {
    if (!account) return
    setBusy(true)
    try {
      const [d, status] = await Promise.all([
        api.digestPreview(account.id),
        api.schedulerStatus(),
      ])
      setDigest({ subject: d.subject, body: d.body })
      setScheduler(status)
    } catch (e) {
      flash((e as Error).message, true)
    } finally {
      setBusy(false)
    }
  }

  const onSendDigest = async () => {
    if (!account) return
    setBusy(true)
    try {
      const res = await api.sendDigest(account.id)
      flash(`Digest ${res.delivery}`)
      setDigest(null)
    } catch (e) {
      flash((e as Error).message, true)
    } finally {
      setBusy(false)
    }
  }

  const onClearData = async () => {
    if (!account) return
    if (!confirm('Delete all transactions for this account? This cannot be undone.'))
      return
    setBusy(true)
    try {
      const res = await api.clearData(account.id)
      flash(`Cleared ${res.deleted} transactions`)
      await loadAnalytics(account.id)
    } catch (e) {
      flash((e as Error).message, true)
    } finally {
      setBusy(false)
    }
  }

  const onCorrect = async (txn: Transaction, category: string) => {
    try {
      await api.correctCategory(txn.id, category)
      await loadAnalytics(account!.id)
      flash(`Recategorized "${txn.merchant}" → ${category}`)
    } catch (e) {
      flash((e as Error).message, true)
    }
  }

  const hasData = txns.length > 0

  if (authed === null)
    return <div className="app-loading">Loading…</div>
  if (!authed) return <AuthScreen onAuthed={() => setAuthed(true)} />

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="logo">EI</div>
          <div>
            <h1>Expense Intelligence</h1>
            <p>{account ? account.name : 'Loading…'}</p>
          </div>
        </div>
        <div className="controls">
          <input
            ref={fileRef}
            type="file"
            accept=".csv"
            style={{ display: 'none' }}
            onChange={(e) => e.target.files?.[0] && onFileSelected(e.target.files[0])}
          />
          {hasData && (
            <button className="ghost" disabled={busy} onClick={onPreviewDigest}>
              Weekly digest
            </button>
          )}
          {hasData && (
            <button className="ghost" disabled={busy} onClick={onClearData}>
              Clear data
            </button>
          )}
          <button disabled={busy} onClick={() => fileRef.current?.click()}>
            {busy ? 'Processing…' : 'Upload statement (CSV)'}
          </button>
          <button className="ghost" onClick={onLogout}>
            Log out
          </button>
        </div>
      </header>

      {!hasData ? (
        <div className="empty">
          <h2>No transactions yet</h2>
          <p>
            Upload a bank or credit-card CSV export to categorize spending and
            surface trends. Try{' '}
            <code>backend/sample_data/sample_statement.csv</code>.
          </p>
        </div>
      ) : (
        <>
          <SummaryCards summary={summary} />

          <AnomaliesPanel anomalies={anomalies} />

          <div className="grid-2">
            <div className="panel">
              <h3>
                Spend by category <span className="hint">this period</span>
              </h3>
              <ResponsiveContainer width="100%" height={300}>
                <PieChart>
                  <Pie
                    data={byCategory}
                    dataKey="total"
                    nameKey="category"
                    innerRadius={70}
                    outerRadius={110}
                    paddingAngle={2}
                  >
                    {byCategory.map((_, i) => (
                      <Cell key={i} fill={COLORS[i % COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    formatter={moneyTooltip}
                    contentStyle={tooltipStyle}
                  />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                </PieChart>
              </ResponsiveContainer>
            </div>

            <div className="panel">
              <h3>
                Monthly trend <span className="hint">spend vs income</span>
              </h3>
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={trends}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2c3947" />
                  <XAxis dataKey="month" stroke="#8b98a9" fontSize={12} />
                  <YAxis stroke="#8b98a9" fontSize={12} />
                  <Tooltip
                    formatter={moneyTooltip}
                    contentStyle={tooltipStyle}
                    cursor={{ fill: 'rgba(255,255,255,0.04)' }}
                  />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="total_income" name="Income" fill="#34d399" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="total_spend" name="Spend" fill="#f87171" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          <SubscriptionsPanel subscriptions={subscriptions} />

          <div className="panel">
            <h3>
              Transactions{' '}
              <span className="hint">
                pick a category to correct it — corrections train the model
              </span>
            </h3>
            <div className="scroll">
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Merchant</th>
                    <th>Category</th>
                    <th style={{ textAlign: 'right' }}>Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {txns.map((t) => (
                    <tr key={t.id}>
                      <td style={{ color: 'var(--text-dim)' }}>{t.txn_date}</td>
                      <td>{t.merchant}</td>
                      <td>
                        <select
                          className="cat-select"
                          value={t.category}
                          onChange={(e) => onCorrect(t, e.target.value)}
                        >
                          {CATEGORIES.map((c) => (
                            <option key={c} value={c}>{c}</option>
                          ))}
                        </select>{' '}
                        <span className={`pill src-${t.category_source}`}>
                          {t.category_source}
                        </span>
                      </td>
                      <td
                        style={{ textAlign: 'right' }}
                        className={t.amount < 0 ? 'amount-out' : 'amount-in'}
                      >
                        {money(t.amount)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {preview && (
        <PreviewModal
          preview={preview}
          fileName={pendingFile?.name ?? ''}
          busy={busy}
          onConfirm={confirmImport}
          onCancel={cancelImport}
        />
      )}

      {digest && (
        <div className="modal-overlay" onClick={() => setDigest(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>Weekly email digest</h2>
            <p className="muted">
              This is what the scheduled job emails out — the same content,
              generated straight from the database.
            </p>
            {scheduler && (
              <div className="sched-status">
                <span>
                  Scheduler: <strong>{scheduler.cadence}</strong>
                </span>
                <span>
                  Delivery: <strong>{scheduler.delivery_mode}</strong>
                </span>
                {scheduler.next_run && (
                  <span>
                    Next run:{' '}
                    <strong>
                      {new Date(scheduler.next_run).toLocaleString()}
                    </strong>
                  </span>
                )}
              </div>
            )}
            <pre className="digest-body">{digest.body}</pre>
            <div className="modal-actions">
              <button className="ghost" onClick={() => setDigest(null)}>
                Close
              </button>
              <button disabled={busy} onClick={onSendDigest}>
                {busy ? 'Sending…' : 'Send now'}
              </button>
            </div>
          </div>
        </div>
      )}

      {toast && <div className={`toast ${toast.err ? 'err' : ''}`}>{toast.msg}</div>}
    </div>
  )
}

function PreviewModal({
  preview,
  fileName,
  busy,
  onConfirm,
  onCancel,
}: {
  preview: SchemaPreview
  fileName: string
  busy: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Column mapping</h2>
        <p className="muted">
          {fileName} — detected {preview.detected_columns.length} columns
        </p>

        {preview.usable ? (
          <>
            <div className="map-grid">
              {Object.entries(preview.mapping).map(([role, col]) => (
                <div className="map-row" key={role}>
                  <span className="role">{ROLE_LABEL[role] ?? role}</span>
                  <span className="arrow">←</span>
                  <span className="col">{col}</span>
                </div>
              ))}
            </div>

            {preview.warnings.map((w, i) => (
              <div className="banner warn" key={i}>⚠ {w}</div>
            ))}

            <h3 className="sub-h">Sample of the expense data</h3>
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Merchant</th>
                  <th style={{ textAlign: 'right' }}>Amount</th>
                </tr>
              </thead>
              <tbody>
                {preview.sample_rows.map((r, i) => (
                  <tr key={i}>
                    <td style={{ color: 'var(--text-dim)' }}>{r.txn_date}</td>
                    <td>{r.merchant}</td>
                    <td
                      style={{ textAlign: 'right' }}
                      className={r.amount < 0 ? 'amount-out' : 'amount-in'}
                    >
                      {money(r.amount)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="modal-actions">
              <button className="ghost" onClick={onCancel}>Cancel</button>
              <button disabled={busy} onClick={onConfirm}>
                {busy ? 'Importing…' : 'Import these transactions'}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="banner err">
              This file can’t be read as a bank statement — it’s missing{' '}
              {preview.missing.join(' and ')}.
            </div>
            <p className="muted">
              The mapper looks for a date, a merchant/description, and an amount.
              Files like fraud-detection or feature datasets (anonymized numeric
              columns, no dates or merchant names) don’t contain spending data to
              import.
            </p>
            <p className="muted" style={{ fontSize: '0.78rem' }}>
              Detected columns: {preview.detected_columns.join(', ')}
            </p>
            <div className="modal-actions">
              <button className="ghost" onClick={onCancel}>Close</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

const tooltipStyle = {
  background: '#222b38',
  border: '1px solid #2c3947',
  borderRadius: 8,
  fontSize: 12,
}

const ANOMALY_LABEL: Record<string, string> = {
  spend_spike: 'Spend spike',
  new_merchant: 'New merchant',
}

function AnomaliesPanel({ anomalies }: { anomalies: Anomaly[] }) {
  if (anomalies.length === 0) return null // hide entirely when all-clear
  return (
    <div className="panel alerts">
      <h3>
        Alerts <span className="hint">{anomalies.length} flagged this period</span>
      </h3>
      <div className="alert-list">
        {anomalies.map((a) => (
          <div className="alert-row" key={a.id}>
            <span className={`tag tag-${a.reason_code}`}>
              {ANOMALY_LABEL[a.reason_code] ?? a.reason_code}
            </span>
            <span className="alert-detail">{a.detail}</span>
            <span className="alert-meta">
              {a.txn_date} · {a.category}
            </span>
            <span className="amount-out alert-amount">{money(a.amount)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function SubscriptionsPanel({ subscriptions }: { subscriptions: Subscription[] }) {
  const monthlyTotal = subscriptions.reduce((sum, s) => {
    const perMonth =
      s.cadence === 'weekly'
        ? s.average_amount * 4.33
        : s.cadence === 'biweekly'
          ? s.average_amount * 2.17
          : s.cadence === 'yearly'
            ? s.average_amount / 12
            : s.cadence === 'quarterly'
              ? s.average_amount / 3
              : s.average_amount // monthly
    return sum + perMonth
  }, 0)

  return (
    <div className="panel">
      <h3>
        Recurring subscriptions{' '}
        <span className="hint">
          {subscriptions.length
            ? `~${money(monthlyTotal)}/mo across ${subscriptions.length}`
            : 'detected automatically from charge cadence'}
        </span>
      </h3>
      {subscriptions.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>
          No recurring charges detected yet. They appear once a merchant bills you
          on a regular cadence (e.g. monthly) for a stable amount.
        </p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Merchant</th>
              <th>Cadence</th>
              <th>Seen</th>
              <th>Next charge</th>
              <th style={{ textAlign: 'right' }}>Amount</th>
            </tr>
          </thead>
          <tbody>
            {subscriptions.map((s) => (
              <tr key={s.id}>
                <td>{s.merchant}</td>
                <td>
                  <span className="pill">{s.cadence}</span>
                </td>
                <td style={{ color: 'var(--text-dim)' }}>{s.occurrences}×</td>
                <td style={{ color: 'var(--text-dim)' }}>
                  {s.next_expected ?? '—'}
                </td>
                <td style={{ textAlign: 'right' }} className="amount-out">
                  {money(-s.average_amount)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function SummaryCards({ summary }: { summary: SummaryStats | null }) {
  if (!summary) return null
  return (
    <div className="cards">
      <div className="card">
        <div className="label">Total spend</div>
        <div className="value neg">{money(summary.total_spend)}</div>
        <div className="sub">{summary.transaction_count} transactions</div>
      </div>
      <div className="card">
        <div className="label">Total income</div>
        <div className="value pos">{money(summary.total_income)}</div>
      </div>
      <div className="card">
        <div className="label">Net</div>
        <div className={`value ${summary.net < 0 ? 'neg' : 'pos'}`}>
          {money(summary.net)}
        </div>
      </div>
      <div className="card">
        <div className="label">Top category</div>
        <div className="value">{summary.top_category ?? '—'}</div>
        <div className="sub">
          {summary.start_date} → {summary.end_date}
        </div>
      </div>
    </div>
  )
}
