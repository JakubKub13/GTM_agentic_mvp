import { useMutation, type UseMutationResult } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { api, type ApiError, type StartRunRequest, type StartRunResponse } from "@/lib/api";

/**
 * Mutation that launches a run (POST /runs, plan #8/#19) and, on success,
 * navigates to the live run view (`/run/:id`).
 *
 * The caller (NewRunForm) owns the real-run confirm gate and error toasting;
 * this hook stays a thin POST + navigate wrapper so it is trivially testable.
 */
export function useStartRun(): UseMutationResult<
  StartRunResponse,
  ApiError,
  StartRunRequest
> {
  const navigate = useNavigate();
  return useMutation<StartRunResponse, ApiError, StartRunRequest>({
    mutationFn: (body) => api.startRun(body),
    onSuccess: ({ run_id }) => {
      navigate(`/run/${run_id}`);
    },
  });
}
