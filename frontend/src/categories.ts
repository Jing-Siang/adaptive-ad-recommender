// Mirrors backend/scripts/generate_seed_campaign_data.py's _CATEGORIES --
// kept in sync by convention, same as STATUS_STYLES/ROLE_LABELS elsewhere
// in this app duplicate their backend-defined counterparts rather than
// fetching them from an API.
export const CAMPAIGN_CATEGORIES = [
  'home_repair',
  'food',
  'finance',
  'electronics',
  'fitness',
  'travel',
  'fashion',
  'automotive',
  'pets',
  'beauty',
  'education',
  'real_estate',
  'parenting',
  'outdoor_recreation',
  'home_decor',
  'software_subscription',
  'alcohol',
  'gambling',
  'baby_products',
  'gaming',
  'furniture',
  'toys',
  'health_supplements',
  'photography',
  'musical_instruments',
  'gardening',
  'jewelry_accessories',
  'wedding_services',
  'job_training_career',
  'streaming_entertainment',
  'insurance',
  'moving_relocation',
] as const

/** "home_repair" -> "Home Repair" -- derived, not hand-mapped, since the
 * category list is too large to keep a manual label map in sync with. */
export function categoryLabel(category: string): string {
  return category.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}
