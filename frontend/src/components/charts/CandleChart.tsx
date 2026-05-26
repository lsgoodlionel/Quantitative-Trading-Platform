import { useEffect, useRef } from "react"
import {
  createChart,
  ColorType,
  CrosshairMode,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
} from "lightweight-charts"
import type { Bar } from "@/types"

interface CandleChartProps {
  bars: Bar[]
  height?: number
}

function toChartData(bars: Bar[]): CandlestickData[] {
  return bars.map((b) => ({
    time: b.time.slice(0, 10) as import("lightweight-charts").Time,
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
  }))
}

export function CandleChart({ bars, height = 400 }: CandleChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#0d1117" },
        textColor: "#8b949e",
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#21262d" },
        horzLines: { color: "#21262d" },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "#58a6ff", labelBackgroundColor: "#1c2536" },
        horzLine: { color: "#58a6ff", labelBackgroundColor: "#1c2536" },
      },
      rightPriceScale: {
        borderColor: "#30363d",
        textColor: "#8b949e",
      },
      timeScale: {
        borderColor: "#30363d",
        timeVisible: true,
        fixLeftEdge: true,
        fixRightEdge: true,
      },
      height,
    })

    const series = chart.addCandlestickSeries({
      upColor: "#3fb950",
      downColor: "#f85149",
      borderUpColor: "#3fb950",
      borderDownColor: "#f85149",
      wickUpColor: "#3fb950",
      wickDownColor: "#f85149",
    })

    chartRef.current = chart
    seriesRef.current = series

    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth })
      }
    })
    ro.observe(containerRef.current)

    return () => {
      ro.disconnect()
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [height])

  useEffect(() => {
    if (!seriesRef.current || bars.length === 0) return
    const data = toChartData(bars)
    seriesRef.current.setData(data)
    chartRef.current?.timeScale().fitContent()
  }, [bars])

  return <div ref={containerRef} style={{ height }} />
}
