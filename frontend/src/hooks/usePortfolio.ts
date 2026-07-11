import { useMutation } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type {
  PortfolioOptRequest, PortfolioOptResult,
  AllocateRequest, AllocateResult,
} from "@/types"

export function usePortfolioOptimize() {
  return useMutation<PortfolioOptResult, Error, PortfolioOptRequest>({
    mutationFn: (req) => api.post<PortfolioOptResult>("/api/v1/portfolio/optimize", req),
  })
}

export function usePortfolioAllocate() {
  return useMutation<AllocateResult, Error, AllocateRequest>({
    mutationFn: (req) => api.post<AllocateResult>("/api/v1/portfolio/allocate", req),
  })
}
