import { useEffect, useRef } from "react"
import {
  createChart,
  ColorType,
  CrosshairMode,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type LineData,
  type HistogramData,
  type Time,
} from "lightweight-charts"
import type { Bar } from "@/types"

interface CandleChartProps {
  bars: Bar[]
  height?: number
  showMA?: boolean
  showVolume?: boolean
  currency?: string
}

/** 计算简单移动均线 */
function calcMA(bars: Bar[], period: number): LineData[] {
  const result: LineData[] = []
  for (let i = period - 1; i < bars.length; i++) {
    const slice = bars.slice(i - period + 1, i + 1)
    const avg = slice.reduce((s, b) => s + b.close, 0) / period
    result.push({
      time: bars[i].time.slice(0, 10) as Time,
      value: parseFloat(avg.toFixed(4)),
    })
  }
  return result
}

function toChartData(bars: Bar[]): CandlestickData[] {
  return bars.map((b) => ({
    time: b.time.slice(0, 10) as Time,
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
  }))
}

function toVolumeData(bars: Bar[]): HistogramData[] {
  return bars.map((b) => ({
    time: b.time.slice(0, 10) as Time,
    value: b.volume,
    color: b.close >= b.open ? "rgba(63,185,80,0.35)" : "rgba(248,81,73,0.35)",
  }))
}

export function CandleChart({
  bars,
  height = 400,
  showMA = true,
  showVolume = true,
}: CandleChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null)
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null)
  const ma5Ref = useRef<ISeriesApi<"Line"> | null>(null)
  const ma20Ref = useRef<ISeriesApi<"Line"> | null>(null)
  const ma60Ref = useRef<ISeriesApi<"Line"> | null>(null)

  // 创建图表（仅挂载时）
  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#0d1117" },
        textColor: "#8b949e",
        fontFamily: "'JetBrains Mono', 'Consolas', monospace",
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
        scaleMargins: showVolume ? { top: 0.08, bottom: 0.28 } : { top: 0.08, bottom: 0.08 },
      },
      timeScale: {
        borderColor: "#30363d",
        timeVisible: true,
        fixLeftEdge: true,
        fixRightEdge: true,
      },
      height,
    })

    // K 线
    const candle = chart.addCandlestickSeries({
      upColor: "#3fb950",
      downColor: "#f85149",
      borderUpColor: "#3fb950",
      borderDownColor: "#f85149",
      wickUpColor: "#3fb950",
      wickDownColor: "#f85149",
    })
    candleRef.current = candle

    // 成交量（叠加在同一 price scale 下方）
    if (showVolume) {
      const vol = chart.addHistogramSeries({
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
      })
      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      })
      volumeRef.current = vol
    }

    // MA 均线
    if (showMA) {
      const ma5 = chart.addLineSeries({
        color: "#f0a500",
        lineWidth: 1,
        title: "MA5",
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      })
      const ma20 = chart.addLineSeries({
        color: "#58a6ff",
        lineWidth: 1,
        title: "MA20",
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      })
      const ma60 = chart.addLineSeries({
        color: "#bc8cff",
        lineWidth: 1,
        title: "MA60",
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      })
      ma5Ref.current = ma5
      ma20Ref.current = ma20
      ma60Ref.current = ma60
    }

    chartRef.current = chart

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
      candleRef.current = null
      volumeRef.current = null
      ma5Ref.current = null
      ma20Ref.current = null
      ma60Ref.current = null
    }
  }, [height, showMA, showVolume])

  // 数据更新
  useEffect(() => {
    if (!candleRef.current || bars.length === 0) return

    candleRef.current.setData(toChartData(bars))

    if (showVolume && volumeRef.current) {
      volumeRef.current.setData(toVolumeData(bars))
    }

    if (showMA) {
      ma5Ref.current?.setData(calcMA(bars, 5))
      ma20Ref.current?.setData(calcMA(bars, 20))
      ma60Ref.current?.setData(calcMA(bars, 60))
    }

    chartRef.current?.timeScale().fitContent()
  }, [bars, showMA, showVolume])

  return <div ref={containerRef} style={{ height }} />
}
