"use client";

import { Suspense, useEffect } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";

/**
 * Legacy route: the "Questions" tab was merged into "Analyse" (/inference). This keeps any
 * bookmark / deep link working by forwarding the query (?search=, ?new=, …).
 */
function Redirect() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const sp = useSearchParams();
  useEffect(() => {
    const query = sp.toString();
    router.replace(`/projects/${id}/inference${query ? `?${query}` : ""}`);
  }, [id, router, sp]);
  return null;
}

export default function SearchesRedirect() {
  return (
    <Suspense fallback={null}>
      <Redirect />
    </Suspense>
  );
}
