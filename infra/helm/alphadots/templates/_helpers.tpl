{{/*
Chart name, truncated and DNS-1123-safe.
*/}}
{{- define "alphadots.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Full release name, used as a prefix for every object this chart creates.
*/}}
{{- define "alphadots.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Common labels, applied to every object.
*/}}
{{- define "alphadots.labels" -}}
helm.sh/chart: {{ include "alphadots.name" . }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: alphadots
{{ include "alphadots.selectorLabels" . }}
{{- end -}}

{{/*
Selector labels for a specific component (pass "api", "redpanda", "web", etc as $component).
Used ALONE on selector/matchLabels/podSelector blocks. Never combine with
"alphadots.labels" in the same labels block -- both already include name/instance, and
pairing them produces duplicate map keys that break strict YAML-to-JSON conversion (the
`helm lint` error this comment is here to prevent a regression of). Where a labels block
needs both the full chart labels AND a component tag, use "alphadots.componentLabel"
instead, below.
*/}}
{{- define "alphadots.componentSelectorLabels" -}}
app.kubernetes.io/name: {{ include "alphadots.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
Just the component tag, for pairing with "alphadots.labels" in a labels: block without
re-emitting name/instance (see note above).
*/}}
{{- define "alphadots.componentLabel" -}}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
Base selector labels (release-wide, no component) -- used on the chart-level labels block.
*/}}
{{- define "alphadots.selectorLabels" -}}
app.kubernetes.io/name: {{ include "alphadots.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Resolve a service image ref: {registry/}{repository}:{tag}. `.image.repository`
defaults to "alphadots/<service key>" when the per-service map does not set one
(it never does today -- every python service shares the one service.Dockerfile --
but the hook is here so a future per-service override needs no template change).
*/}}
{{- define "alphadots.serviceImage" -}}
{{- $root := .root -}}
{{- $service := .service -}}
{{- $repo := printf "alphadots/%s" $service -}}
{{- if $root.Values.image.registry -}}
{{- printf "%s/%s:%s" $root.Values.image.registry $repo $root.Values.image.tag -}}
{{- else -}}
{{- printf "%s:%s" $repo $root.Values.image.tag -}}
{{- end -}}
{{- end -}}
