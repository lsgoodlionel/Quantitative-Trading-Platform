import { useMutation, useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"

export interface PresetStrategy {
  name: string
  description: string
}

export interface ValidateResult {
  valid: boolean
  errors: string[]
  warnings: string[]
}

export function usePresets() {
  return useQuery<PresetStrategy[]>({
    queryKey: ["strategy-presets"],
    queryFn: () => api.get<PresetStrategy[]>("/api/v1/strategies/presets"),
    staleTime: Infinity,
  })
}

export function useStrategySource(name: string | null) {
  return useQuery<{ name: string; source: string }>({
    queryKey: ["strategy-source", name],
    queryFn: () => api.get(`/api/v1/strategies/source/${name}`),
    enabled: name !== null,
    staleTime: Infinity,
  })
}

export function useValidateStrategy() {
  return useMutation<ValidateResult, Error, { code: string }>({
    mutationFn: (req) => api.post<ValidateResult>("/api/v1/strategies/validate", req),
  })
}
