export type Overview = {
  week_start_date: string
  total_spend_usd: number
  advertiser_count: number
  wow_change_pct: number | null
  loaded_total_spend_usd: number
  loaded_advertisers: number
}

export type Trend = {
  week_start_date: string
  total_spend_usd: number
  advertiser_count: number
  wow_change_pct: number | null
}

export type Advertiser = {
  advertiser_id: string
  advertiser_name: string
  total_spend_usd: number
  weeks_observed: number
  last_observed_week: string
  share_of_observed_spend_pct: number
  spend_rank: number
}

export type AdvertiserPage = {
  items: Advertiser[]
  limit: number
  offset: number
  has_more: boolean
}

export type GeographyStatus = {
  available: boolean
  reason: string
}

export type Forecast = {
  advertiser_id: string
  data_cutoff_week: string
  target_week: string
  point: number
  p10: number
  p50: number
  p90: number
  model_version: string
  model_run_id: string
  actual_usd: number | null
}

export type ForecastPage = {
  items: Forecast[]
  limit: number
  offset: number
  has_more: boolean
}

export type PipelineHealth = {
  run_id: string
  finished_at_utc: string
  status: string
  blocking_failures: number
  gold_latest_week: string
}

type Param = string | number | undefined

export async function apiGet<T>(
  path: string,
  params: Record<string, Param> = {},
): Promise<T> {
  const query = new URLSearchParams()

  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') {
      query.set(key, String(value))
    }
  }

  const suffix = query.size ? `?${query}` : ''
  const response = await fetch(`/api/v1${path}${suffix}`, {
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(10_000),
  })

  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const body = await response.json()
      detail = body.detail ?? detail
    } catch {
      // Lỗi mạng/proxy có thể không trả JSON
    }
    throw new Error(detail)
  }

  return response.json() as Promise<T>
}