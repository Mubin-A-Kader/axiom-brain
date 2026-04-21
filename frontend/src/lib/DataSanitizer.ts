/**
 * Axiom Data Integrity Utility
 * Ensures all result sets are type-safe for visualization.
 */

export function sanitizeChartData(data: any, xCol: string, yCols: string[]) {
  if (!data || !data.rows || !data.columns) return [];
  
  const xIdx = data.columns.findIndex((c: string) => c.toLowerCase() === xCol?.toLowerCase());
  const safeXIdx = xIdx === -1 ? 0 : xIdx;
  
  return data.rows.map((row: any[]) => {
    const obj: any = {};
    const xVal = row[safeXIdx];
    
    // X-Axis is always treated as a string for categorical/time series display
    obj[xCol] = String(xVal !== null && xVal !== undefined ? xVal : '');
    
    yCols.forEach(yCol => {
      const yIdx = data.columns.findIndex((c: string) => c.toLowerCase() === yCol?.toLowerCase());
      const safeYIdx = yIdx === -1 ? (data.columns.length > 1 ? 1 : 0) : yIdx;
      
      const rawVal = row[safeYIdx];
      
      // The "Ghost Killer": Force numeric conversion for Y-Axis
      let numVal = Number(rawVal);
      if (isNaN(numVal) || rawVal === null || rawVal === undefined) {
        numVal = 0;
      }
      obj[yCol] = numVal;
    });
    return obj;
  });
}

/**
 * Validates if a dataset is empty or corrupted
 */
export function isDataEmpty(data: any): boolean {
  if (!data) return true;
  if (Array.isArray(data) && data.length === 0) return true;
  if (data.rows && Array.isArray(data.rows) && data.rows.length === 0) return true;
  return false;
}
