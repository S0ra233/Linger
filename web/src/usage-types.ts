export type CostPart = 'output' | 'cache_read' | 'uncached_input' | 'cache_write' | 'flat_input'
export type CostParts = Partial<Record<CostPart, string | null>>

export interface CacheComparison {
  input_cost: string | null
  uncached_input_cost: string | null
  difference: string | null
  records?: number
  excluded_records?: number
}
