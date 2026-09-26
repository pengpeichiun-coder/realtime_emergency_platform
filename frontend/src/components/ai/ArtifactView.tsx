export function ArtifactView({ result }: { result: Record<string, unknown> }) {
  const content = result.content
  if (!content || typeof content !== 'object' || Array.isArray(content)) {
    return (
      <p role="alert">The summary is not available in a supported format.</p>
    )
  }
  return (
    <section
      aria-label="AI summary result"
      className="mt-4 space-y-4 rounded-lg border border-amber-200 bg-amber-50/40 p-4"
    >
      <p className="font-semibold text-amber-900">
        AI-generated · Human review required
      </p>
      <p className="text-xs text-slate-600">
        Provider: {String(result.provider)} · Model: {String(result.model)} ·
        Prompt: {String(result.prompt_version)}
      </p>
      {result.provider === 'fake' && (
        <p className="text-sm text-amber-900">
          Deterministic demo output — no external AI provider was called.
        </p>
      )}
      {Object.entries(content).map(([key, value]) => (
        <div key={key}>
          <h3 className="text-sm font-semibold capitalize">
            {key.replaceAll('_', ' ')}
          </h3>
          {Array.isArray(value) ? (
            value.length ? (
              <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
                {value.map((item, index) => (
                  <li key={index}>{String(item)}</li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-slate-500">None recorded.</p>
            )
          ) : (
            <p className="mt-1 whitespace-pre-wrap text-sm">{String(value)}</p>
          )}
        </div>
      ))}
    </section>
  )
}
