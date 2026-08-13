/**
 * 模型管理页测试（V3 Wave B-a / I0）
 *
 * 对应契约 docs/contracts/waveBa-llm-gateway.md §五 验收 4：
 * 页面渲染、测试连接的三种结果态、留空不覆盖密钥。
 */
import { describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

import {
  describeTestResult,
  providerStateLabel,
  providerStateTone,
  type ProviderStatus,
  type TestResult,
} from "@/hooks/useLlmConfig"
import { ProviderCard } from "@/pages/settings/ProviderCard"
import { ActiveModelBar } from "@/pages/settings/ActiveModelBar"
import { OllamaOnboarding } from "@/pages/settings/OllamaOnboarding"

const OLLAMA: ProviderStatus = {
  id: "ollama",
  label: "Ollama（本地）",
  kind: "openai_compat",
  requires_key: false,
  doc_url: "https://ollama.com/download",
  suggested_models: ["qwen2.5:14b", "llama3.1:8b"],
  supports_model_listing: true,
  default_base_url: "http://localhost:11434/v1",
  configured: true,
  ready: true,
  base_url: "http://localhost:11434/v1",
  default_model: "qwen2.5:14b",
  key_hint: null,
}

const OPENAI: ProviderStatus = {
  id: "openai",
  label: "OpenAI",
  kind: "openai_compat",
  requires_key: true,
  doc_url: "https://platform.openai.com/api-keys",
  suggested_models: ["gpt-4o", "gpt-4o-mini"],
  supports_model_listing: true,
  default_base_url: "https://api.openai.com/v1",
  configured: true,
  ready: true,
  base_url: "https://api.openai.com/v1",
  default_model: "gpt-4o",
  key_hint: "sk••••••••1234",
}

type CardProps = Parameters<typeof ProviderCard>[0]

function renderCard(overrides: Partial<CardProps> = {}) {
  const handlers = {
    onSave: vi.fn<CardProps["onSave"]>(),
    onDelete: vi.fn<CardProps["onDelete"]>(),
    onTest: vi.fn<CardProps["onTest"]>(),
    onFetchModels: vi.fn<CardProps["onFetchModels"]>(),
  }
  render(
    <ProviderCard
      provider={OLLAMA}
      isActive={false}
      defaultExpanded
      canEdit
      busy={false}
      testResult={null}
      fetchedModels={null}
      {...handlers}
      {...overrides}
    />,
  )
  return handlers
}

// ── 展示辅助函数 ──────────────────────────────────────────────────────────────

describe("providerStateLabel / Tone", () => {
  it("marks a ready provider as usable", () => {
    expect(providerStateLabel(OLLAMA)).toBe("可用")
    expect(providerStateTone(OLLAMA)).toBe("ok")
  })

  it("distinguishes never-configured from configured-but-missing-key", () => {
    const untouched = { ...OPENAI, configured: false, ready: false, key_hint: null }
    const keyless = { ...OPENAI, ready: false, key_hint: null }

    expect(providerStateLabel(untouched)).toBe("未配置")
    expect(providerStateTone(untouched)).toBe("idle")
    expect(providerStateLabel(keyless)).toBe("缺少密钥")
    expect(providerStateTone(keyless)).toBe("warn")
  })
})

describe("describeTestResult", () => {
  const base: TestResult = {
    ok: true, provider_id: "ollama", model: "qwen2.5:14b", latency_ms: 812.4, error: null,
  }

  it("reports model and latency on success", () => {
    expect(describeTestResult(base)).toBe("连接成功 · qwen2.5:14b · 812 ms")
  })

  it("surfaces the vendor error verbatim on failure", () => {
    expect(
      describeTestResult({ ...base, ok: false, error: "Incorrect API key provided" }),
    ).toBe("Incorrect API key provided")
  })

  it("falls back to a generic message when the error is missing", () => {
    expect(describeTestResult({ ...base, ok: false, error: null })).toBe("连接失败")
  })
})

// ── 卡片渲染 ─────────────────────────────────────────────────────────────────

describe("ProviderCard", () => {
  it("renders address, model and the no-key badge for Ollama", () => {
    renderCard()

    expect(screen.getByLabelText("Ollama（本地） 访问地址")).toHaveValue(
      "http://localhost:11434/v1",
    )
    expect(screen.getByLabelText("Ollama（本地） 默认模型")).toHaveValue("qwen2.5:14b")
    expect(screen.getByText("无需密钥")).toBeInTheDocument()
  })

  it("shows the masked key as a placeholder and never prefills the input", () => {
    renderCard({ provider: OPENAI })

    const input = screen.getByLabelText("OpenAI API 密钥") as HTMLInputElement
    expect(input.value).toBe("")
    expect(input.placeholder).toContain("sk••••••••1234")
    expect(input.placeholder).toContain("留空则不修改")
  })

  it("omits api_key entirely when the field is left blank", () => {
    // 留空 = 保持原值。发一个空串上去会让后端多一条语义分支，这里从源头掐掉。
    const props = renderCard({ provider: OPENAI })

    fireEvent.change(screen.getByLabelText("OpenAI 访问地址"), {
      target: { value: "https://proxy.internal/v1" },
    })
    fireEvent.click(screen.getByRole("button", { name: "保存" }))

    expect(props.onSave).toHaveBeenCalledWith({
      base_url: "https://proxy.internal/v1",
      default_model: "gpt-4o",
    })
    expect(props.onSave.mock.calls[0][0]).not.toHaveProperty("api_key")
  })

  it("sends the api_key when the user actually typed one", () => {
    const props = renderCard({ provider: OPENAI })

    fireEvent.change(screen.getByLabelText("OpenAI API 密钥"), {
      target: { value: "sk-brand-new-key" },
    })
    fireEvent.click(screen.getByRole("button", { name: "保存" }))

    expect(props.onSave).toHaveBeenCalledWith({
      base_url: "https://api.openai.com/v1",
      default_model: "gpt-4o",
      api_key: "sk-brand-new-key",
    })
  })

  it("clears the key field after saving so it is never re-sent by accident", () => {
    renderCard({ provider: OPENAI })
    const input = screen.getByLabelText("OpenAI API 密钥") as HTMLInputElement

    fireEvent.change(input, { target: { value: "sk-brand-new-key" } })
    fireEvent.click(screen.getByRole("button", { name: "保存" }))

    expect(input.value).toBe("")
  })

  it("accepts a model name outside the suggestion list", () => {
    // suggested_models 是建议不是白名单
    const props = renderCard()

    fireEvent.change(screen.getByLabelText("Ollama（本地） 默认模型"), {
      target: { value: "my-private-finetune:local-2026" },
    })
    fireEvent.click(screen.getByRole("button", { name: "保存" }))

    expect(props.onSave.mock.calls[0][0].default_model).toBe("my-private-finetune:local-2026")
  })

  it("passes the currently edited model to the test action", () => {
    const props = renderCard()

    fireEvent.change(screen.getByLabelText("Ollama（本地） 默认模型"), {
      target: { value: "llama3.1:8b" },
    })
    fireEvent.click(screen.getByRole("button", { name: "测试连接" }))

    expect(props.onTest).toHaveBeenCalledWith("llama3.1:8b")
  })

  // ── 测试连接的三种结果态 ──
  it("renders the idle state with no result banner", () => {
    renderCard()
    expect(screen.queryByRole("status")).toBeNull()
  })

  it("renders the success state", () => {
    renderCard({
      testResult: {
        ok: true, provider_id: "ollama", model: "qwen2.5:14b", latency_ms: 120, error: null,
      },
    })

    const banner = screen.getByRole("status")
    expect(banner.textContent).toContain("连接成功")
    expect(banner.className).toContain("#3fb950")
  })

  it("renders the failure state with the vendor message", () => {
    renderCard({
      testResult: {
        ok: false, provider_id: "ollama", model: null, latency_ms: null,
        error: "无法连接 http://localhost:11434/v1/chat/completions：Connection refused",
      },
    })

    const banner = screen.getByRole("status")
    expect(banner.textContent).toContain("Connection refused")
    expect(banner.className).toContain("#f85149")
  })

  it("disables every write action for read-only roles", () => {
    renderCard({ canEdit: false })

    for (const name of ["保存", "测试连接", "删除配置"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled()
    }
  })

  it("blocks testing and model pulling until the provider is saved", () => {
    renderCard({ provider: { ...OLLAMA, configured: false, ready: false } })

    expect(screen.getByRole("button", { name: "测试连接" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "拉取模型" })).toBeDisabled()
    expect(screen.queryByRole("button", { name: "删除配置" })).toBeNull()
  })

  it("collapses and expands on header click", () => {
    renderCard({ defaultExpanded: false })
    const header = screen.getByRole("button", { expanded: false })

    expect(screen.queryByLabelText("Ollama（本地） 访问地址")).toBeNull()
    fireEvent.click(header)
    expect(screen.getByLabelText("Ollama（本地） 访问地址")).toBeInTheDocument()
  })

  it("offers pulled models alongside the suggested ones", () => {
    const { container } = render(
      <ProviderCard
        provider={OLLAMA}
        isActive={false}
        defaultExpanded
        canEdit
        busy={false}
        testResult={null}
        fetchedModels={["deepseek-r1:14b", "qwen2.5:14b"]}
        onSave={vi.fn()}
        onDelete={vi.fn()}
        onTest={vi.fn()}
        onFetchModels={vi.fn()}
      />,
    )

    const options = [...container.querySelectorAll("datalist option")].map((o) =>
      o.getAttribute("value"),
    )
    expect(options).toContain("deepseek-r1:14b")
    expect(options).toContain("llama3.1:8b")
    // 拉取到的和建议里重复的模型只出现一次
    expect(options.filter((o) => o === "qwen2.5:14b")).toHaveLength(1)
  })
})

// ── 当前生效选择器 ────────────────────────────────────────────────────────────

describe("ActiveModelBar", () => {
  const ACTIVE = {
    configured: true,
    provider_id: "ollama",
    provider_label: "Ollama（本地）",
    model: "qwen2.5:14b",
    explicit: false,
  }

  it("renders nothing when no provider is ready", () => {
    const { container } = render(
      <ActiveModelBar
        active={{ configured: false, provider_id: null, provider_label: null, model: null, explicit: false }}
        readyProviders={[]}
        canEdit
        busy={false}
        onApply={vi.fn()}
      />,
    )
    expect(container.firstChild).toBeNull()
  })

  it("explains that the selection was automatic", () => {
    render(
      <ActiveModelBar
        active={ACTIVE}
        readyProviders={[OLLAMA]}
        canEdit
        busy={false}
        onApply={vi.fn()}
      />,
    )
    expect(screen.getByText(/自动选择/)).toBeInTheDocument()
  })

  it("keeps the switch button disabled until something actually changed", () => {
    render(
      <ActiveModelBar
        active={ACTIVE}
        readyProviders={[OLLAMA, OPENAI]}
        canEdit
        busy={false}
        onApply={vi.fn()}
      />,
    )
    expect(screen.getByRole("button", { name: "切换" })).toBeDisabled()
  })

  it("applies the chosen provider and model", () => {
    const onApply = vi.fn()
    render(
      <ActiveModelBar
        active={ACTIVE}
        readyProviders={[OLLAMA, OPENAI]}
        canEdit
        busy={false}
        onApply={onApply}
      />,
    )

    fireEvent.change(screen.getByLabelText("生效服务商"), { target: { value: "openai" } })
    fireEvent.click(screen.getByRole("button", { name: "切换" }))

    expect(onApply).toHaveBeenCalledWith("openai", "gpt-4o")
  })

  it("allows a free-form model name for the active selection", () => {
    const onApply = vi.fn()
    render(
      <ActiveModelBar
        active={ACTIVE}
        readyProviders={[OLLAMA]}
        canEdit
        busy={false}
        onApply={onApply}
      />,
    )

    fireEvent.change(screen.getByLabelText("生效模型"), {
      target: { value: "whatever-i-pulled:latest" },
    })
    fireEvent.click(screen.getByRole("button", { name: "切换" }))

    expect(onApply).toHaveBeenCalledWith("ollama", "whatever-i-pulled:latest")
  })

  it("disables switching for read-only roles", () => {
    render(
      <ActiveModelBar
        active={ACTIVE}
        readyProviders={[OLLAMA, OPENAI]}
        canEdit={false}
        busy={false}
        onApply={vi.fn()}
      />,
    )

    fireEvent.change(screen.getByLabelText("生效服务商"), { target: { value: "openai" } })
    expect(screen.getByRole("button", { name: "切换" })).toBeDisabled()
  })
})

// ── 空状态引导 ────────────────────────────────────────────────────────────────

describe("OllamaOnboarding", () => {
  it("leads with the no-API-key local path", () => {
    render(<OllamaOnboarding />)

    expect(screen.getByText(/无需任何 API 密钥/)).toBeInTheDocument()
    expect(screen.getByText("http://localhost:11434/v1")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /ollama.com\/download/ })).toHaveAttribute(
      "href",
      "https://ollama.com/download",
    )
  })
})
