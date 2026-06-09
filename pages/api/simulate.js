/* ============================================================
   API Route — /api/simulate

   Runs Monte Carlo on the server so heavy computation
   does not block the browser thread.
   ============================================================ */

import {
  runMonteCarloSimulation,
  normalizeSimulationInput
} from '../../lib/monteCarlo';

export default function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({
      ok: false,
      error: 'Method not allowed. Use POST.'
    });
  }

  try {
    const input = normalizeSimulationInput(req.body);
    const result = runMonteCarloSimulation(input);

    if (!result.ok) {
      return res.status(400).json({
        ok: false,
        errors: result.errors || ['Invalid simulation input.']
      });
    }

    return res.status(200).json(result);
  } catch (error) {
    console.error('[simulate] error:', error);

    return res.status(500).json({
      ok: false,
      error: 'Simulation failed — ' + error.message
    });
  }
}
