@description('Deployment location')
param location string

@description('Tags applied to the alert group')
param tags object = {}

@description('Environment name')
param environment string

@description('Managed Prometheus Azure Monitor Workspace resource ID')
param prometheusWorkspaceResourceId string

@description('External SLI probe name used in Prometheus labels')
param probeName string

@description('Action Group resource ID for publisher freshness alert')
param actionGroupId string = ''

resource publisherHeartbeatAlert 'Microsoft.AlertsManagement/prometheusRuleGroups@2023-03-01' = {
  name: 'external-sli-publisher-alerts-${environment}'
  location: location
  tags: tags
  properties: {
    description: 'External SLI publisher freshness guardrail'
    scopes: [prometheusWorkspaceResourceId]
    enabled: true
    interval: 'PT1M'
    rules: [
      {
        alert: 'ExternalSliPublisherHeartbeatMissing'
        expression: 'absent_over_time(chaos_app_external_sli_publisher_heartbeat{environment="${environment}",service="chaos-app",test="${probeName}"}[20m])'
        for: 'PT5M'
        annotations: {
          description: 'External SLI publisher heartbeat has not reached Managed Prometheus for 20 minutes. Azure Monitor SLI input may become stale.'
        }
        enabled: true
        severity: 2
        resolveConfiguration: {
          autoResolved: true
          timeToResolve: 'PT10M'
        }
        labels: {
          severity: 'warning'
          alert_type: 'sli-signal-health'
          source: 'external-sli-publisher'
        }
        actions: actionGroupId != ''
          ? [
              {
                actionGroupId: actionGroupId
              }
            ]
          : []
      }
    ]
  }
}
