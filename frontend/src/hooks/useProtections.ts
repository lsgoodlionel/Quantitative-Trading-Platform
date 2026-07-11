import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { ActiveLock, ProtectionsConfig } from "@/types"

interface ActiveLocksResponse {
  locks: ActiveLock[]
  count: number
}

/** GET /api/v1/protections/config */
export function useProtectionsConfig() {
  return useQuery<ProtectionsConfig>({
    queryKey: ["protections-config"],
    queryFn: () => api.get<ProtectionsConfig>("/api/v1/protections/config"),
  })
}

/** PUT /api/v1/protections/config */
export function useUpdateProtectionsConfig() {
  const qc = useQueryClient()
  return useMutation<ProtectionsConfig, Error, ProtectionsConfig>({
    mutationFn: (config) =>
      api.put<ProtectionsConfig>("/api/v1/protections/config", config),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["protections-config"] })
      qc.invalidateQueries({ queryKey: ["protections-locks"] })
    },
  })
}

/** GET /api/v1/protections/locks (轮询 15s) */
export function useActiveLocks() {
  return useQuery<ActiveLocksResponse>({
    queryKey: ["protections-locks"],
    queryFn: () => api.get<ActiveLocksResponse>("/api/v1/protections/locks"),
    refetchInterval: 15_000,
  })
}

/** DELETE /api/v1/protections/locks/{id} */
export function useClearLock() {
  const qc = useQueryClient()
  return useMutation<{ cleared: string }, Error, string>({
    mutationFn: (lockId) =>
      api.delete<{ cleared: string }>(`/api/v1/protections/locks/${lockId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["protections-locks"] }),
  })
}
