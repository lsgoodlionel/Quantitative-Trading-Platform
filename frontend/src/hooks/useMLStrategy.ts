import { useMutation } from "@tanstack/react-query"
import { api } from "@/lib/api"

export type MLModelType =
  | "logistic_regression"
  | "random_forest"
  | "gradient_boosting"
  | "double_ensemble"

/** DoubleEnsemble 专属集成诊断（仅 model_type=double_ensemble 时返回） */
export interface EnsembleDiagnostics {
  num_models: number
  enable_sr: boolean
  enable_fs: boolean
  sub_feature_counts: number[]
  feature_usage: { name: string; used_by: number }[]
}

export interface MLTrainRequest {
  symbol: string
  market: string
  frequency: string
  start?: string | null
  end?: string | null
  model_type: MLModelType
  forward_days: number
  test_size: number
}

export interface FeatureImportance {
  name: string
  importance: number
}

export interface PredictionPoint {
  time: string
  actual: number
  predicted: number
  probability: number
}

export interface MLTrainResult {
  symbol: string
  market: string
  model_type: MLModelType
  forward_days: number
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

  cv_mean: number
  cv_std: number

  /** 仅 DoubleEnsemble 模型返回 */
  ensemble?: EnsembleDiagnostics
}

export function useMLTrain() {
  return useMutation<MLTrainResult, Error, MLTrainRequest>({
    mutationFn: (req) => api.post<MLTrainResult>("/api/v1/quant/ml/train", req),
  })
}
