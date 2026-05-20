targetScope = 'resourceGroup'

@description('Principal ID of the user-assigned managed identity used by Azure Monitor SLI')
param sliIdentityPrincipalId string

@description('Name of the Managed Prometheus Azure Monitor Workspace')
param prometheusWorkspaceName string

var monitoringMetricsPublisherRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '3913510d-42f4-4e42-8a64-420c390055eb'
)
var monitoringReaderRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '43d0d8ad-25c7-4714-9337-8ba259a9fe05'
)

resource prometheusWorkspaceManagedDataCollectionRule 'Microsoft.Insights/dataCollectionRules@2024-03-11' existing = {
  name: prometheusWorkspaceName
}

resource prometheusWorkspaceManagedDcrMetricsPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(
    prometheusWorkspaceManagedDataCollectionRule.id,
    sliIdentityPrincipalId,
    monitoringMetricsPublisherRoleDefinitionId
  )
  scope: prometheusWorkspaceManagedDataCollectionRule
  properties: {
    roleDefinitionId: monitoringMetricsPublisherRoleDefinitionId
    principalId: sliIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource prometheusWorkspaceManagedDcrMonitoringReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(
    prometheusWorkspaceManagedDataCollectionRule.id,
    sliIdentityPrincipalId,
    monitoringReaderRoleDefinitionId
  )
  scope: prometheusWorkspaceManagedDataCollectionRule
  properties: {
    roleDefinitionId: monitoringReaderRoleDefinitionId
    principalId: sliIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}
