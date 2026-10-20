import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { expect, test } from '@playwright/test'

type Check = { sql: number; api: number; match: boolean }
type Evidence = {
  warehouse_sha256: string
  week_filter: string
  checks: {
    latest_week_spend_usd: Check
    latest_week_advertisers: Check
    loaded_total_spend_usd: Check
  }
}

test('web thật khớp API và SQL, lưu ảnh năm màn hình', async ({
  page,
  request,
}) => {
  test.skip(process.env.RUN_LIVE !== '1', 'Chỉ chạy khi API local đang bật')

  const root = path.resolve(process.cwd(), '../..')
  const evidence = JSON.parse(
    readFileSync(path.join(root, 'reports/week14/evidence.json'), 'utf8'),
  ) as Evidence
  const warehouseHash = createHash('sha256')
    .update(readFileSync(path.join(root, 'data/tmp/week4/warehouse.duckdb')))
    .digest('hex')
  expect(warehouseHash).toBe(evidence.warehouse_sha256)
  const folder = path.join(root, 'reports/week15/screenshots')
  mkdirSync(folder, { recursive: true })

  const apiResponse = await request.get('http://127.0.0.1:8000/api/v1/overview')
  expect(apiResponse.ok()).toBeTruthy()
  const api = await apiResponse.json()
  expect(api.week_start_date).toBe(evidence.week_filter)

  // SQL được đối soát và hash nguồn lưu trong evidence tuần 14
  for (const check of Object.values(evidence.checks)) {
    expect(check.match).toBe(true)
    expect(check.api).toBe(check.sql)
  }
  expect(api.total_spend_usd).toBe(evidence.checks.latest_week_spend_usd.sql)
  expect(api.advertiser_count).toBe(
    evidence.checks.latest_week_advertisers.sql,
  )
  expect(api.loaded_total_spend_usd).toBe(
    evidence.checks.loaded_total_spend_usd.sql,
  )

  const pageErrors: string[] = []
  const directCloudRequests: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  page.on('request', (request) => {
    if (/bigquery\.googleapis\.com|storage\.googleapis\.com/.test(request.url())) {
      directCloudRequests.push(request.url())
    }
  })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
  const usd = (value: number) => `${value.toLocaleString('en-US')} USD`
  await expect(page.getByText(usd(api.total_spend_usd))).toBeVisible()
  await expect(page.getByText(usd(api.loaded_total_spend_usd))).toBeVisible()
  await expect(page.getByRole('img', { name: /Observed advertiser spend/ })).toBeVisible()

  const cardValue = async (heading: string) => {
    const card = page.getByRole('article').filter({
      has: page.getByRole('heading', { name: heading }),
    })
    const text = await card.locator('strong').innerText()
    return Number(text.replace(/[^\d.-]/g, ''))
  }
  const webSpend = await cardValue('Weekly spend')
  const webAdvertisers = await cardValue('Advertisers observed')
  const webLoadedSpend = await cardValue('Loaded spend (all weeks)')
  expect(webSpend).toBe(evidence.checks.latest_week_spend_usd.sql)
  expect(webAdvertisers).toBe(evidence.checks.latest_week_advertisers.sql)
  expect(webLoadedSpend).toBe(evidence.checks.loaded_total_spend_usd.sql)
  await page.screenshot({ path: path.join(folder, '01_overview_local.png'), fullPage: true })

  await page.getByRole('link', { name: 'Advertisers' }).click()
  await expect(page.getByRole('heading', { name: 'Advertiser Explorer' })).toBeVisible()
  await expect(page.locator('tbody tr').first()).toBeVisible()
  await page.screenshot({ path: path.join(folder, '02_advertisers_local.png'), fullPage: true })

  await page.getByRole('link', { name: 'Geography & Targeting' }).click()
  await expect(page.getByRole('heading', { name: 'Chưa có geo mart' })).toBeVisible()
  await page.screenshot({ path: path.join(folder, '03_geography_local.png'), fullPage: true })

  await page.getByRole('link', { name: 'Forecasts' }).click()
  await expect(page.getByText('Pending actual').first()).toBeVisible()
  await page.screenshot({ path: path.join(folder, '04_forecasts_local.png'), fullPage: true })

  await page.getByRole('link', { name: 'Pipeline Health' }).click()
  await expect(page.getByText('Gold latest week')).toBeVisible()
  await page.screenshot({ path: path.join(folder, '05_pipeline_local.png'), fullPage: true })

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await expect(page.getByText(usd(api.total_spend_usd))).toBeVisible()
  const horizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  )
  expect(horizontalOverflow).toBe(false)
  await page.screenshot({ path: path.join(folder, '06_overview_mobile_local.png'), fullPage: true })
  expect(pageErrors).toEqual([])
  expect(directCloudRequests).toEqual([])

  writeFileSync(
    path.join(root, 'reports/week15/evidence.json'),
    JSON.stringify(
      {
        status: 'PASS_LOCAL_WEB_API_SQL',
        data_mode: 'LOCAL_SNAPSHOT',
        power_bi_status: 'NOT_VERIFIED',
        generated_at_utc: new Date().toISOString(),
        warehouse_sha256: warehouseHash,
        direct_cloud_requests: directCloudRequests.length,
        week_filter: evidence.week_filter,
        checks: {
          weekly_spend_usd: {
            sql: evidence.checks.latest_week_spend_usd.sql,
            api: api.total_spend_usd,
            web: webSpend,
          },
          advertiser_count: {
            sql: evidence.checks.latest_week_advertisers.sql,
            api: api.advertiser_count,
            web: webAdvertisers,
          },
          loaded_total_spend_usd: {
            sql: evidence.checks.loaded_total_spend_usd.sql,
            api: api.loaded_total_spend_usd,
            web: webLoadedSpend,
          },
        },
        screenshots: [
          '01_overview_local.png',
          '02_advertisers_local.png',
          '03_geography_local.png',
          '04_forecasts_local.png',
          '05_pipeline_local.png',
          '06_overview_mobile_local.png',
        ],
      },
      null,
      2,
    ) + '\n',
  )
})
