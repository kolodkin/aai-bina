import type { OrderCol } from './QueryView'

// Saved presentation payload: empty selections persist as null ("no selection"),
// matching the backend's nullable columns.
export function presentationForSave(
  orderBy: OrderCol[],
  visibleCols: string[],
): { order_by: OrderCol[] | null; fields: string[] | null } {
  return {
    order_by: orderBy.length ? orderBy : null,
    fields: visibleCols.length ? visibleCols : null,
  }
}
