{{- define "unibot-sandbox.name" -}}
unibot-sandbox
{{- end }}

{{- define "unibot-sandbox.labels" -}}
app.kubernetes.io/name: {{ include "unibot-sandbox.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{- define "unibot-sandbox.operatorServiceAccount" -}}
{{ .Release.Name }}-operator
{{- end }}
