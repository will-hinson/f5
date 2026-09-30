{{- define "f5.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "f5.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "f5.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "f5.selectorLabels" -}}
app.kubernetes.io/name: {{ include "f5.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "f5.labels" -}}
helm.sh/chart: {{ include "f5.chart" . }}
{{ include "f5.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Whether any AWS files are mounted (existing Secret or chart-created) */}}
{{- define "f5.awsSecretEnabled" -}}
{{- if or .Values.aws.existingSecret .Values.aws.config .Values.aws.credentials .Values.aws.caBundle -}}
true
{{- end -}}
{{- end }}

{{- define "f5.awsSecretName" -}}
{{- default (printf "%s-aws" (include "f5.fullname" .)) .Values.aws.existingSecret }}
{{- end }}

{{- define "f5.awsCaBundleEnabled" -}}
{{- if or .Values.aws.caBundle (and .Values.aws.existingSecret .Values.aws.existingSecretHasCaBundle) -}}
true
{{- end -}}
{{- end }}
