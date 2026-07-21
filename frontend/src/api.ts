const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export interface AdCandidate {
  ad_id: string
  headline: string
  description: string
  category: string
  price: number | null
  similarity_score: number
}

export interface RankedAd {
  ad_id: string
  relevance_score: number
  justification: string
}

export interface GuardrailResult {
  ad_id: string
  allowed: boolean
  reason: string | null
}

export interface RecommendationTrace {
  user_id: string
  candidates: AdCandidate[]
  rankings: RankedAd[]
  guardrail_results: GuardrailResult[]
  served_ad_id: string | null
}

export async function fetchRecommendation(userId: string, topK = 10): Promise<RecommendationTrace> {
  const response = await fetch(`${API_BASE_URL}/recommend`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, top_k: topK }),
  })
  if (!response.ok) {
    throw new Error(`recommend request failed: ${response.status}`)
  }
  return response.json()
}
