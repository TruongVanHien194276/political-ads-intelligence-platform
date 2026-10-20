import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  // Mock ở ranh giới HTTP để test UI ổn định, không cần Gold DB trong CI
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname

    const fixtures: Record<string, unknown> = {
      '/api/v1/overview': {
        week_start_date: '2026-10-11',
        total_spend_usd: 65042100,
        advertiser_count: 2923,
        wow_change_pct: -18.63,
        loaded_total_spend_usd: 609155500,
        loaded_advertisers: 5060,
      },
      '/api/v1/trends': [
        {
          week_start_date: '2026-10-04',
          total_spend_usd: 79932300,
          advertiser_count: 2629,
          wow_change_pct: null,
        },
        {
          week_start_date: '2026-10-11',
          total_spend_usd: 65042100,
          advertiser_count: 2923,
          wow_change_pct: -18.63,
        },
      ],
      '/api/v1/advertisers': {
        items: [{
          advertiser_id: 'AR1',
          advertiser_name: 'Demo Advertiser',
          total_spend_usd: 250000,
          weeks_observed: 2,
          last_observed_week: '2026-10-11',
          share_of_observed_spend_pct: 0.04,
          spend_rank: 1,
        }],
        limit: 20,
        offset: 0,
        has_more: false,
      },
      '/api/v1/advertisers/AR1': {
        advertiser_id: 'AR1',
        advertiser_name: 'Demo Advertiser',
        total_spend_usd: 250000,
        weeks_observed: 2,
        last_observed_week: '2026-10-11',
        share_of_observed_spend_pct: 0.04,
        spend_rank: 1,
      },
      '/api/v1/geography': {
        available: false,
        reason: 'Gold hiện chưa có geo mart',
      },
      '/api/v1/forecasts': {
        items: [{
          advertiser_id: 'AR1',
          data_cutoff_week: '2026-10-11',
          target_week: '2026-10-18',
          point: 250000,
          p10: 200000,
          p50: 250000,
          p90: 300000,
          model_version: '1',
          model_run_id: 'test-run',
          actual_usd: null,
        }],
        limit: 100,
        offset: 0,
        has_more: false,
      },
      '/api/v1/pipeline-health': {
        run_id: 'test-run',
        finished_at_utc: '2026-10-20T05:18:48Z',
        status: 'PASS',
        blocking_failures: 0,
        gold_latest_week: '2026-10-11',
      },
    }

    if (!(path in fixtures)) {
      await route.fulfill({ status: 404, json: { detail: 'Not found' } })
      return
    }

    await route.fulfill({ json: fixtures[path] })
  })
})

test('năm màn hình và drill-down', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
  await expect(page.getByText('65,042,100 USD')).toBeVisible()

  await page.getByRole('combobox', { name: 'Tuần' }).selectOption('2026-10-11')
  await expect(page).toHaveURL(/week=2026-10-11/)
  await page.reload()
  await expect(page.getByRole('combobox', { name: 'Tuần' })).toHaveValue(
    '2026-10-11',
  )

  await page.getByRole('link', { name: 'Advertisers' }).click()
  await page.getByRole('combobox', { name: 'Sắp xếp' }).selectOption('name_asc')
  await expect(page).toHaveURL(/sort=name_asc/)
  await page.getByRole('link', { name: 'Demo Advertiser' }).click()
  await expect(page.getByText('ID: AR1')).toBeVisible()

  await page.getByRole('link', { name: 'Geography & Targeting' }).click()
  await expect(
    page.getByRole('heading', { name: 'Chưa có geo mart' }),
  ).toBeVisible()

  await page.getByRole('link', { name: 'Forecasts' }).click()
  await expect(page.getByText('Pending actual')).toBeVisible()

  await page.getByRole('link', { name: 'Pipeline Health' }).click()
  await expect(page.getByText('Gold latest week')).toBeVisible()
})

test('API lỗi thì hiện error state', async ({ page }) => {
  await page.route('**/api/v1/overview*', async (route) => {
    await route.fulfill({
      status: 503,
      json: { detail: 'Gold warehouse chưa có trên máy' },
    })
  })

  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText(
    'Gold warehouse chưa có trên máy',
  )
})

test('loading và empty state hiển thị đúng', async ({ page }) => {
  await page.route('**/api/v1/overview*', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 700))
    await route.fulfill({
      json: {
        week_start_date: '2026-10-11',
        total_spend_usd: 100,
        advertiser_count: 1,
        wow_change_pct: null,
        loaded_total_spend_usd: 100,
        loaded_advertisers: 1,
      },
    })
  })
  await page.route('**/api/v1/forecasts*', async (route) => {
    await route.fulfill({
      json: { items: [], limit: 100, offset: 0, has_more: false },
    })
  })

  await page.goto('/')
  await expect(page.getByRole('status', { name: '' }).first()).toContainText(
    'Đang tải dữ liệu',
  )
  await expect(
    page.getByRole('article').filter({ hasText: 'Weekly spend' }),
  ).toContainText('100 USD')

  await page.getByRole('link', { name: 'Forecasts' }).click()
  await expect(page.getByText('Không có forecast cho kỳ đã chọn')).toBeVisible()
})
