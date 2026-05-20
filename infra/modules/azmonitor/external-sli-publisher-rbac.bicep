@description('Managed Prometheus Azure Monitor Workspace resource ID')
param prometheusWorkspaceResourceId string

@description('Managed Prometheus Data Collection Rule resource ID used by remote-write')
param prometheusDataCollectionRuleId string

@description('External SLI publisher Function App name')
param publisherName string

@description('External SLI publisher managed identity principal ID')
param publisherPrincipalId string

var monitoringMetricsPublisherRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '3913510d-42f4-4e42-8a64-420c390055eb'
)
var monitoringReaderRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '43d0d8ad-25c7-4714-9337-8ba259a9fe05'
)

resource prometheusWorkspace 'Microsoft.Monitor/accounts@2023-04-03' existing = {
  name: last(split(prometheusWorkspaceResourceId, '/'))
}

resource prometheusDataCollectionRule 'Microsoft.Insights/dataCollectionRules@2024-03-11' existing = {
  name: last(split(prometheusDataCollectionRuleId, '/'))
}

resource functionPrometheusWorkspaceMonitoringReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(prometheusWorkspace.id, publisherName, monitoringReaderRoleDefinitionId)
  scope: prometheusWorkspace
  properties: {
    roleDefinitionId: monitoringReaderRoleDefinitionId
    principalId: publisherPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource functionPrometheusDcrMetricsPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(prometheusDataCollectionRule.id, publisherName, monitoringMetricsPublisherRoleDefinitionId)
  scope: prometheusDataCollectionRule
  properties: {
    roleDefinitionId: monitoringMetricsPublisherRoleDefinitionId
    principalId: publisherPrincipalId
    principalType: 'ServicePrincipal'
  }
}
