{{/*
chart.image — the SHARED image-ref helper every consumer chart template must use.
Given an images: entry {registry, repository, tag}, render the full ref. The
onboard job rewrites the images: block to point at our ECR by DIGEST in overrides.yaml,
so the SAME helper then renders the vendored ECR ref — a chart that hardcodes a ref
instead of using this helper escapes the rewrite and ImagePullBackOffs on the no-NAT VPC.
Kept in AGENT.md.template so every chart copies it verbatim.

tag joins with ":" normally, but with "" (no separator) when it is a digest — the
onboard override sets tag to "@sha256:..." so this renders "registry/repository@sha256:..."
(a colon there would be invalid). Empty registry => the source registry's default.
*/}}
{{- define "chart.image" -}}
{{- $sep := ":" -}}
{{- if hasPrefix "@" .tag -}}{{- $sep = "" -}}{{- end -}}
{{- if .registry -}}
{{ .registry }}/{{ .repository }}{{ $sep }}{{ .tag }}
{{- else -}}
{{ .repository }}{{ $sep }}{{ .tag }}
{{- end -}}
{{- end -}}
