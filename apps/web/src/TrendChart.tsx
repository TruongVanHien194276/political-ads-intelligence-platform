import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import type { Trend } from './api'

echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer])

export default function TrendChart({ rows }: { rows: Trend[] }) {
  const element = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!element.current) return

    const chart = echarts.init(element.current)

    chart.setOption({
      tooltip: {
        trigger: 'axis',
        valueFormatter: (value: unknown) =>
          `${Number(value).toLocaleString('en-US')} USD`,
      },
      grid: { left: 72, right: 20, top: 25, bottom: 55 },
      xAxis: {
        type: 'category',
        data: rows.map((row) => row.week_start_date),
        axisLabel: { rotate: 35 },
      },
      yAxis: {
        type: 'value',
        name: 'USD',
        axisLabel: {
          formatter: (value: number) => `${(value / 1_000_000).toFixed(0)}m`,
        },
      },
      series: [
        {
          name: 'Observed spend',
          type: 'line',
          smooth: false,
          data: rows.map((row) => row.total_spend_usd),
        },
      ],
    })

    // Chart phải resize khi panel đổi kích thước, không chỉ khi window resize
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(element.current)

    return () => {
      observer.disconnect()
      chart.dispose()
    }
  }, [rows])

  return (
    <div
      className="chart"
      ref={element}
      role="img"
      aria-label="Observed advertiser spend theo tuần, đơn vị USD"
    />
  )
}