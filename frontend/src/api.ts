import type {
  Account,
  BatchRecommendationResponse,
  CampaignCreateRequest,
  CampaignResponse,
  ChatMessage,
  ModerationRequest,
  OnboardingCheckpointResponse,
  PerformanceResponse,
  Reaction,
  ReportCategory,
  UserResponse,
} from './types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

// In-memory copy of the access token, plus its localStorage persistence --
// both live here, not in AuthContext, specifically because the automatic
// silent-refresh in request() below updates this token with no way for
// AuthContext to "hear" that moment. Keeping storage co-located with the
// state it stores means every path that changes the token (explicit login,
// explicit logout, or a transparent background refresh) stays in sync with
// localStorage automatically, not just the ones AuthContext itself calls.
const ACCESS_TOKEN_STORAGE_KEY = 'access_token'
let accessToken: string | null = null

export function setAccessToken(token: string | null): void {
  accessToken = token
  if (token) {
    localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, token)
  } else {
    localStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY)
  }
}

/** Called once at app startup to prime the in-memory token from whatever
 * was already in localStorage, before the first request goes out -- avoids
 * an unnecessary round trip (request with no token -> 401 -> refresh) when
 * the cached token happens to still be valid. */
export function loadStoredAccessToken(): string | null {
  accessToken = localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)
  return accessToken
}

interface AuthTokenResponse {
  access_token: string
  user: Account
}

/** Direct fetch, not request() -- avoids recursing back into the 401-retry
 * logic below. credentials: 'include' is what actually sends the httpOnly
 * refresh cookie along, since it's on a different concern than the
 * Authorization header. */
export async function refreshAccessToken(): Promise<AuthTokenResponse | null> {
  const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
    method: 'POST',
    credentials: 'include',
  })
  if (!response.ok) return null
  const body: AuthTokenResponse = await response.json()
  setAccessToken(body.access_token)
  return body
}

async function request<T>(path: string, init?: RequestInit, _isRetry = false): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`

  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers,
    credentials: 'include',
    ...init,
  })

  if (response.status === 401 && !_isRetry && path !== '/auth/refresh') {
    // Access token expired mid-session -- try one silent refresh (the
    // httpOnly cookie rides along automatically) before giving up.
    const refreshed = await refreshAccessToken()
    if (refreshed) return request<T>(path, init, true)
  }

  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new Error(`${init?.method ?? 'GET'} ${path} failed: ${response.status} ${body}`)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return response.json()
}

// --------------------------------------------------------------------------
// Auth
// --------------------------------------------------------------------------

export async function googleLogin(idToken: string): Promise<AuthTokenResponse> {
  const body: AuthTokenResponse = await request('/auth/google', {
    method: 'POST',
    body: JSON.stringify({ id_token: idToken }),
  })
  setAccessToken(body.access_token)
  return body
}

export async function logout(): Promise<void> {
  try {
    await request('/auth/logout', { method: 'POST' })
  } finally {
    setAccessToken(null)
  }
}

export function fetchMe(): Promise<Account> {
  return request('/auth/me')
}

// --------------------------------------------------------------------------
// Serving: ads, feed, reactions
// --------------------------------------------------------------------------

export function fetchRecommendationBatch(batchSize = 10): Promise<BatchRecommendationResponse> {
  return request('/recommend/batch', {
    method: 'POST',
    body: JSON.stringify({ batch_size: batchSize }),
  })
}

export function logImpression(adId: string): Promise<void> {
  return request('/events/impression', {
    method: 'POST',
    body: JSON.stringify({ ad_id: adId }),
  })
}

export function sendReaction(adId: string, reaction: Reaction): Promise<void> {
  return request('/events/reaction', {
    method: 'POST',
    body: JSON.stringify({ ad_id: adId, reaction }),
  })
}

export function clearReaction(adId: string): Promise<void> {
  return request('/events/reaction', {
    method: 'DELETE',
    body: JSON.stringify({ ad_id: adId }),
  })
}

export function sendReport(adId: string, category: ReportCategory, reason?: string): Promise<void> {
  return request('/events/report', {
    method: 'POST',
    body: JSON.stringify({ ad_id: adId, category, reason }),
  })
}

export function doNotShowAgain(adId: string): Promise<void> {
  return request('/users/me/do-not-show', {
    method: 'POST',
    body: JSON.stringify({ ad_id: adId }),
  })
}

// --------------------------------------------------------------------------
// Users / profiles
// --------------------------------------------------------------------------

export function createUser(interestSummary: string): Promise<UserResponse> {
  return request('/users/me', {
    method: 'POST',
    body: JSON.stringify({ interest_summary: interestSummary }),
  })
}

export function getUser(): Promise<UserResponse> {
  return request('/users/me')
}

export function resetProfile(): Promise<void> {
  return request('/users/me/reset', { method: 'POST' })
}

// --------------------------------------------------------------------------
// Onboarding chat
// --------------------------------------------------------------------------

export function onboardingCheckpoint(messages: ChatMessage[]): Promise<OnboardingCheckpointResponse> {
  return request('/onboarding/checkpoint', {
    method: 'POST',
    body: JSON.stringify({ messages }),
  })
}

/** Marks the account as having actually finished onboarding -- distinct
 * from "has a profile vector," which gets seeded much earlier (see the
 * backend endpoint's docstring). Returns the updated account so callers
 * can sync AuthContext without a second round trip. */
export function completeOnboarding(): Promise<Account> {
  return request('/onboarding/complete', { method: 'POST' })
}

/**
 * Streams the onboarding chat reply, calling onDelta with each text chunk as
 * it arrives. readyToFinish tells the model to wrap up and guide the user to
 * their feed instead of asking another question. No candidates param -- the
 * reply doesn't change based on whether candidates are shown this turn (see
 * OnboardingChatRequest's docstring on the backend for why).
 */
export async function streamOnboardingChat(
  messages: ChatMessage[],
  readyToFinish: boolean,
  onDelta: (chunk: string) => void,
  signal?: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`

  const response = await fetch(`${API_BASE_URL}/onboarding/chat`, {
    method: 'POST',
    headers,
    credentials: 'include',
    body: JSON.stringify({ messages, ready_to_finish: readyToFinish }),
    signal,
  })
  if (!response.ok || !response.body) {
    throw new Error(`onboarding/chat failed: ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    onDelta(decoder.decode(value, { stream: true }))
  }
}

// --------------------------------------------------------------------------
// Performance dashboard
// --------------------------------------------------------------------------

export function fetchPerformance(): Promise<PerformanceResponse> {
  return request('/performance')
}

// --------------------------------------------------------------------------
// Campaigns: submission + moderation
// --------------------------------------------------------------------------

export function createCampaign(payload: CampaignCreateRequest): Promise<CampaignResponse> {
  return request('/campaigns', { method: 'POST', body: JSON.stringify(payload) })
}

export function listCampaigns(status?: string): Promise<CampaignResponse[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : ''
  return request(`/campaigns${query}`)
}

export function moderateCampaign(id: number, payload: ModerationRequest): Promise<CampaignResponse> {
  return request(`/campaigns/${id}/moderate`, { method: 'POST', body: JSON.stringify(payload) })
}
