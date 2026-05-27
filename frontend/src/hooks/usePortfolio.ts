import { useMutation } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { PortfolioOptRequest, PortfolioOptResult } from "@/types"

export function usePortfolioOptimize() {
  return useMutation<PortfolioOptResult, Error, PortfolioOptRequest>({
    mutationFn: (req) => api.post<PortfolioOptResult>("/api/v1/portfolio/optimize", req),
  })
}
