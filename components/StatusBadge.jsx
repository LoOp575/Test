/* ============================================================
   StatusBadge — colour-coded trading signal indicator
   ============================================================ */

import { getStatusClass, getStatusLabel } from '../lib/utils';

export default function StatusBadge({ status = 'NO_SHORT' }) {
  return (
    <div className={`status-badge ${getStatusClass(status)}`}>
      {getStatusLabel(status)}
    </div>
  );
}
