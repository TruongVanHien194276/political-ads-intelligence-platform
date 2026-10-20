import { useQuery } from '@tanstack/react-query'
import { lazy, Suspense, type ReactNode } from 'react'
import {
  Link,
  NavLink,
  Route,
  Routes,
  useParams,
  useSearchParams,
} from 'react-router'

import {
  apiGet,
  type Advertiser,
  type AdvertiserPage,
  type ForecastPage,
  type GeographyStatus,
  type Overview as OverviewData,
  type PipelineHealth,
  type Trend,
} from './api'
// ECharts khá lớn; chỉ tải chart khi người xem mở Overview
const TrendChart = lazy(() => import('./TrendChart'))

const usd = (value: number) =>
  `${new Intl.NumberFormat('en-US', {
    maximumFractionDigits: 0,
  }).format(value)} USD`

function Panel({
  pending,
  error,
  children,
}: {
  pending: boolean
  error: Error | null
  children: ReactNode
}) {
  if (pending) return <p role="status">Đang tải dữ liệu…</p>
  if (error) return <p role="alert">Không tải được: {error.message}</p>
  return <>{children}</>
}

function Layout() {
  const health = useQuery({
    queryKey: ['health-badge'],
    queryFn: () => apiGet<PipelineHealth>('/pipeline-health'),
    refetchInterval: 30_000,
  })

  return (
    <div className="shell">
      <header>
        <h1>Political Ads Intelligence</h1>
        <p>
          {health.data
            ? `Local snapshot · Gold đến tuần ${health.data.gold_latest_week}`
            : 'Local snapshot · trạng thái dữ liệu chưa xác nhận'}
        </p>
      </header>

      <nav aria-label="Main navigation">
        <NavLink to="/">Overview</NavLink>
        <NavLink to="/advertisers">Advertisers</NavLink>
        <NavLink to="/geography">Geography & Targeting</NavLink>
        <NavLink to="/forecasts">Forecasts</NavLink>
        <NavLink to="/pipeline">Pipeline Health</NavLink>
      </nav>

      <main>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/advertisers" element={<Advertisers />} />
          <Route path="/advertisers/:id" element={<AdvertiserDetail />} />
          <Route path="/geography" element={<Geography />} />
          <Route path="/forecasts" element={<Forecasts />} />
          <Route path="/pipeline" element={<Pipeline />} />
          <Route path="*" element={<p>Không tìm thấy trang</p>} />
        </Routes>
      </main>

      <footer>
        Observed USD spend trong các tuần đã tải · Không phải toàn bộ
        chi tiêu chính trị · Không có CTR, ROI hoặc conversion
      </footer>
    </div>
  )
}

function Overview() {
  const [search, setSearch] = useSearchParams()
  const week = search.get('week') ?? ''

  const overview = useQuery({
    queryKey: ['overview', week],
    queryFn: () =>
      apiGet<OverviewData>('/overview', { week: week || undefined }),
  })
  const trends = useQuery({
    queryKey: ['trends', 52],
    queryFn: () => apiGet<Trend[]>('/trends', { limit: 52 }),
  })

  return (
    <section>
      <h2>Overview</h2>
      <label>
        Tuần
        <select
          value={week}
          onChange={(event) =>
            setSearch(event.target.value ? { week: event.target.value } : {})
          }
        >
          <option value="">Mới nhất</option>
          {trends.data?.map((row) => (
            <option key={row.week_start_date} value={row.week_start_date}>
              {row.week_start_date}
            </option>
          ))}
        </select>
      </label>

      <Panel pending={overview.isPending} error={overview.error}>
        {overview.data && (
          <>
            <p className="caption">
              Tuần {overview.data.week_start_date} · USD quan sát trong Gold
            </p>
            <div className="cards">
              <article>
                <h3>Weekly spend</h3>
                <strong>{usd(overview.data.total_spend_usd)}</strong>
              </article>
              <article>
                <h3>Advertisers observed</h3>
                <strong>{overview.data.advertiser_count}</strong>
              </article>
              <article>
                <h3>Loaded spend (all weeks)</h3>
                <strong>{usd(overview.data.loaded_total_spend_usd)}</strong>
              </article>
              <article>
                <h3>WoW</h3>
                <strong>
                  {overview.data.wow_change_pct === null
                    ? 'Chưa có kỳ trước'
                    : `${overview.data.wow_change_pct}%`}
                </strong>
              </article>
            </div>
          </>
        )}
      </Panel>

      <h3>Trend theo tuần</h3>
      <Panel pending={trends.isPending} error={trends.error}>
        {trends.data?.length ? (
          <Suspense fallback={<p role="status">Đang tải biểu đồ…</p>}>
            <TrendChart rows={trends.data} />
          </Suspense>
        ) : (
          <p>Chưa có tuần nào để vẽ</p>
        )}
      </Panel>
      <p className="caption">
        Filter tuần phía trên đổi KPI; biểu đồ vẫn hiển thị toàn bộ các tuần
        đã tải để giữ ngữ cảnh
      </p>
    </section>
  )
}

function Advertisers() {
  const [search, setSearch] = useSearchParams()
  const sort = search.get('sort') === 'name_asc' ? 'name_asc' : 'spend_desc'
  const rawPage = Number(search.get('page') ?? '1')
  const page = Number.isInteger(rawPage)
    ? Math.min(101, Math.max(1, rawPage))
    : 1
  const limit = 20
  const offset = (page - 1) * limit

  const result = useQuery({
    queryKey: ['advertisers', sort, offset],
    queryFn: () =>
      apiGet<AdvertiserPage>('/advertisers', { sort, limit, offset }),
  })

  return (
    <section>
      <h2>Advertiser Explorer</h2>
      <label>
        Sắp xếp
        <select
          value={sort}
          onChange={(event) =>
            setSearch({ sort: event.target.value, page: '1' })
          }
        >
          <option value="spend_desc">Spend giảm dần</option>
          <option value="name_asc">Tên A–Z</option>
        </select>
      </label>

      <Panel pending={result.isPending} error={result.error}>
        {result.data?.items.length ? (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Advertiser</th>
                    <th>Observed spend</th>
                    <th>Weeks</th>
                    <th>Share</th>
                  </tr>
                </thead>
                <tbody>
                  {result.data.items.map((row) => (
                    <tr key={row.advertiser_id}>
                      <td>
                        <Link to={`/advertisers/${row.advertiser_id}`}>
                          {row.advertiser_name}
                        </Link>
                      </td>
                      <td>{usd(row.total_spend_usd)}</td>
                      <td>{row.weeks_observed}</td>
                      <td>{row.share_of_observed_spend_pct}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pager">
              <button
                disabled={page === 1}
                onClick={() => setSearch({ sort, page: String(page - 1) })}
              >
                Trước
              </button>
              <span>Trang {page}</span>
              <button
                disabled={!result.data.has_more}
                onClick={() => setSearch({ sort, page: String(page + 1) })}
              >
                Sau
              </button>
            </div>
          </>
        ) : (
          <p>Không có advertiser ở trang này</p>
        )}
      </Panel>
      <p className="caption">
        Tổng spend là trên các tuần đã tải, không phải tổng chi tiêu mọi thời kỳ
      </p>
    </section>
  )
}

function AdvertiserDetail() {
  const { id } = useParams()
  const detail = useQuery({
    queryKey: ['advertiser-detail', id],
    queryFn: () =>
      apiGet<Advertiser>(`/advertisers/${encodeURIComponent(id ?? '')}`),
    enabled: Boolean(id),
  })

  return (
    <section>
      <Link to="/advertisers">← Danh sách advertiser</Link>
      <Panel pending={detail.isPending} error={detail.error}>
        {detail.data && (
          <>
            <h2>{detail.data.advertiser_name}</h2>
            <p>ID: {detail.data.advertiser_id}</p>
            <p>Observed spend: {usd(detail.data.total_spend_usd)}</p>
            <p>Weeks observed: {detail.data.weeks_observed}</p>
            <p>Last observed week: {detail.data.last_observed_week}</p>
          </>
        )}
      </Panel>
    </section>
  )
}

function Geography() {
  const geo = useQuery({
    queryKey: ['geography'],
    queryFn: () => apiGet<GeographyStatus>('/geography'),
  })

  return (
    <section>
      <h2>Geography & Targeting</h2>
      <Panel pending={geo.isPending} error={geo.error}>
        {geo.data?.available === false ? (
          <div className="notice">
            <h3>Chưa có geo mart</h3>
            <p>{geo.data.reason}</p>
            <p>
              Targeting cũng chưa có mart phục vụ API, nên chưa vẽ bản đồ,
              phân khúc hay ROI
            </p>
          </div>
        ) : (
          <p>Kiểm tra contract địa lý trước khi thêm biểu đồ</p>
        )}
      </Panel>
    </section>
  )
}

function Forecasts() {
  const [search, setSearch] = useSearchParams()
  const targetWeek = search.get('target_week') ?? ''
  const rawPage = Number(search.get('page') ?? '1')
  const page = Number.isInteger(rawPage)
    ? Math.min(501, Math.max(1, rawPage))
    : 1
  const limit = 20
  const offset = (page - 1) * limit

  const forecasts = useQuery({
    queryKey: ['forecasts', targetWeek, offset],
    queryFn: () =>
      apiGet<ForecastPage>('/forecasts', {
        target_week: targetWeek || undefined,
        limit,
        offset,
      }),
  })

  const goToPage = (nextPage: number) => {
    setSearch({
      ...(targetWeek ? { target_week: targetWeek } : {}),
      page: String(nextPage),
    })
  }

  return (
    <section>
      <h2>Forecast & Anomalies</h2>
      <label>
        Target week
        <input
          type="date"
          value={targetWeek}
          onChange={(event) =>
            setSearch(
              event.target.value
                ? { target_week: event.target.value, page: '1' }
                : {},
            )
          }
        />
      </label>

      <Panel pending={forecasts.isPending} error={forecasts.error}>
        {forecasts.data?.items.length ? (
          <>
            <div className="table-wrap">
              <table>
              <thead>
                <tr>
                  <th>Advertiser ID</th>
                  <th>Point</th>
                  <th>P10–P90</th>
                  <th>Actual</th>
                  <th>Trạng thái</th>
                </tr>
              </thead>
              <tbody>
                {forecasts.data.items.map((row) => {
                  // Chỉ đánh dấu lệch khoảng dự báo khi actual đã xuất hiện
                  const outside =
                    row.actual_usd !== null &&
                    (row.actual_usd < row.p10 ||
                      row.actual_usd > row.p90)

                  return (
                    <tr key={`${row.advertiser_id}-${row.target_week}`}>
                      <td>{row.advertiser_id}</td>
                      <td>{usd(row.point)}</td>
                      <td>{usd(row.p10)} – {usd(row.p90)}</td>
                      <td>
                        {row.actual_usd === null
                          ? 'Chưa có'
                          : usd(row.actual_usd)}
                      </td>
                      <td>
                        {row.actual_usd === null
                          ? 'Pending actual'
                          : outside
                            ? 'Ngoài khoảng dự báo'
                            : 'Trong khoảng dự báo'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
              </table>
            </div>
            <div className="pager">
              <button disabled={page === 1} onClick={() => goToPage(page - 1)}>
                Trước
              </button>
              <span>Trang {page}</span>
              <button
                disabled={!forecasts.data.has_more}
                onClick={() => goToPage(page + 1)}
              >
                Sau
              </button>
            </div>
          </>
        ) : (
          <p>Không có forecast cho kỳ đã chọn</p>
        )}
      </Panel>
      <p className="caption">
        P10–P90 là khoảng dự báo, không phải cảnh báo gian lận hay kết luận
        nguyên nhân. Model hiện hành là baseline đã qua quality gate Tuần 13
      </p>
    </section>
  )
}

function Pipeline() {
  const health = useQuery({
    queryKey: ['pipeline-page'],
    queryFn: () => apiGet<PipelineHealth>('/pipeline-health'),
    refetchInterval: 30_000,
  })

  return (
    <section>
      <h2>Pipeline Health</h2>
      <Panel pending={health.isPending} error={health.error}>
        {health.data && (
          <div className="cards">
            <article>
              <h3>Last run</h3>
              <strong>{health.data.status}</strong>
              <p>{health.data.run_id}</p>
            </article>
            <article>
              <h3>Blocking failures</h3>
              <strong>{health.data.blocking_failures}</strong>
            </article>
            <article>
              <h3>Gold latest week</h3>
              <strong>{health.data.gold_latest_week}</strong>
            </article>
            <article>
              <h3>Pipeline finished</h3>
              <strong>
                {new Date(health.data.finished_at_utc).toLocaleString()}
              </strong>
            </article>
          </div>
        )}
      </Panel>
      <p className="caption">
        Health được hỏi lại mỗi 30 giây; dữ liệu Gold vẫn là snapshot local,
        không phải streaming live
      </p>
    </section>
  )
}

export default Layout
