@description('Principal ID of the user-assigned managed identity used by Azure Monitor SLI')
param sliIdentityPrincipalId string

@description('Name of the Managed Prometheus Azure Monitor Workspace')
param prometheusWorkspaceName string

@description('Resource ID of the Managed Prometheus Data Collection Rule')
param prometheusDataCollectionRuleId string

var monitoringReaderRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '43d0d8ad-25c7-4714-9337-8ba259a9fe05'
)
var monitoringDataReaderRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'b0d8363b-8ddd-447d-831f-62ca05bff136'
)
var monitoringMetricsPublisherRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '3913510d-42f4-4e42-8a64-420c390055eb'
)
var prometheusDataCollectionRuleName = last(split(prometheusDataCollectionRuleId, '/'))

resource prometheusWorkspace 'Microsoft.Monitor/accounts@2023-04-03' existing = {
  name: prometheusWorkspaceName
}

resource prometheusDataCollectionRule 'Microsoft.Insights/dataCollectionRules@2024-03-11' existing = {
  name: prometheusDataCollectionRuleName
}

resource prometheusWorkspaceMonitoringReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(prometheusWorkspace.id, sliIdentityPrincipalId, monitoringReaderRoleDefinitionId)
  scope: prometheusWorkspace
  properties: {
    roleDefinitionId: monitoringReaderRoleDefinitionId
    principalId: sliIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource prometheusWorkspaceMonitoringDataReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(prometheusWorkspace.id, sliIdentityPrincipalId, monitoringDataReaderRoleDefinitionId)
  scope: prometheusWorkspace
  properties: {
    roleDefinitionId: monitoringDataReaderRoleDefinitionId
    principalId: sliIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource prometheusWorkspaceMetricsPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(prometheusWorkspace.id, sliIdentityPrincipalId, monitoringMetricsPublisherRoleDefinitionId)
  scope: prometheusWorkspace
  properties: {
    roleDefinitionId: monitoringMetricsPublisherRoleDefinitionId
    principalId: sliIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource prometheusDcrMonitoringReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(prometheusDataCollectionRule.id, sliIdentityPrincipalId, monitoringReaderRoleDefinitionId)
  scope: prometheusDataCollectionRule
  properties: {
    roleDefinitionId: monitoringReaderRoleDefinitionId
    principalId: sliIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource prometheusDcrMetricsPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(prometheusDataCollectionRule.id, sliIdentityPrincipalId, monitoringMetricsPublisherRoleDefinitionId)
  scope: prometheusDataCollectionRule
  properties: {
    roleDefinitionId: monitoringMetricsPublisherRoleDefinitionId
    principalId: sliIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}
