import { useMutation, useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { FeatureImportance, PredictionPoint } from "@/hooks/useMLStrategy"

// ── 类型定义（B8 序列模型：LSTM / GRU / ALSTM）──────────────────

export type SequenceModelType = "lstm" | "gru" | "alstm"

export interface SequenceModelMeta {
  value: SequenceModelType
  label: string
  desc: string
}

/** GET /quant/ml/sequence-models 响应 */
export interface SequenceModelsInfo {
  torch_ready: boolean
  install_hint: string
  models: SequenceModelMeta[]
}

export interface SequenceTrainRequest {
  symbol: string
  market: string
  frequency: string
  start?: string | null
  end?: string | null
  model_type: SequenceModelType
  forward_days: number
  seq_len: number
  epochs: number
  hidden_size: number
  num_layers: number
  learning_rate?: number
  dropout?: number
  test_size: number
}

export interface SequenceTrainResult {
  symbol: string
  market: string
  model_type: SequenceModelType
  forward_days: number
  seq_len: number
  epochs: number
  hidden_size: number
  num_layers: number
  n_samples: number
  n_features: number
  feature_names: string[]

  train_accuracy: number
  test_accuracy: number
  precision: number
  recall: number
  f1_score: number
  auc_roc: number

  feature_importance: FeatureImportance[]
  confusion_matrix: [[number, number], [number, number]]
  predictions: PredictionPoint[]

  recent_signal: "BUY" | "SELL" | "NEUTRAL"
  recent_prob: number

  train_loss_curve: number[]
  val_loss_curve: number[]
}

/** 查询可用序列模型 + torch 就绪状态 */
export function useSequenceModels() {
  return useQuery<SequenceModelsInfo, Error>({
    queryKey: ["sequence-models"],
    queryFn: () => api.get<SequenceModelsInfo>("/api/v1/quant/ml/sequence-models"),
    staleTime: 5 * 60_000,
  })
}

/** 训练序列模型 */
export function useSequenceTrain() {
  return useMutation<SequenceTrainResult, Error, SequenceTrainRequest>({
    mutationFn: (req) =>
      api.post<SequenceTrainResult>("/api/v1/quant/ml/sequence-train", req),
  })
}
