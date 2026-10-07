/**
 * Normalize a list response so callers always get a plain array, whether the
 * endpoint is DRF-paginated (`{ count, next, previous, results }`) or returns a
 * bare array (custom APIViews). Added in Phase 1 to fix leave lists breaking
 * after global pagination (50/page) was introduced.
 *
 * @param {import('axios').AxiosResponse} response
 * @returns {Array}
 */
export const unwrapPaginated = (response) =>
  Array.isArray(response.data) ? response.data : (response.data?.results ?? []);
