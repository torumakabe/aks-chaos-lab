@description('Resource ID of the Azure Service Group')
param serviceGroupId string

@description('AKS cluster name')
param aksName string

@description('Managed Prometheus Azure Monitor Workspace name')
param prometheusWorkspaceName string

@description('Application telemetry Azure Monitor Workspace name')
param appAzureMonitorWorkspaceName string

@description('Log Analytics workspace name')
param logAnalyticsWorkspaceName string

@description('Application Insights component name')
param applicationInsightsName string

@description('Azure Managed Redis name')
param redisName string

@description('Azure Container Registry name')
param containerRegistryName string

#disable-next-line BCP081
resource aksCluster 'Microsoft.ContainerService/managedClusters@2026-03-02-preview' existing = {
  name: aksName
}

resource prometheusWorkspace 'Microsoft.Monitor/accounts@2023-04-03' existing = {
  name: prometheusWorkspaceName
}

resource appAzureMonitorWorkspace 'Microsoft.Monitor/accounts@2023-04-03' existing = {
  name: appAzureMonitorWorkspaceName
}

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2025-07-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: applicationInsightsName
}

resource redisEnterprise 'Microsoft.Cache/redisEnterprise@2025-07-01' existing = {
  name: redisName
}

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2025-11-01' existing = {
  name: containerRegistryName
}

resource aksMembership 'Microsoft.Relationships/serviceGroupMember@2023-09-01-preview' = {
  name: 'sgm-${uniqueString(serviceGroupId, aksCluster.id)}'
  scope: aksCluster
  properties: {
    targetId: serviceGroupId
  }
}

resource prometheusWorkspaceMembership 'Microsoft.Relationships/serviceGroupMember@2023-09-01-preview' = {
  name: 'sgm-${uniqueString(serviceGroupId, prometheusWorkspace.id)}'
  scope: prometheusWorkspace
  properties: {
    targetId: serviceGroupId
  }
}

resource appAzureMonitorWorkspaceMembership 'Microsoft.Relationships/serviceGroupMember@2023-09-01-preview' = {
  name: 'sgm-${uniqueString(serviceGroupId, appAzureMonitorWorkspace.id)}'
  scope: appAzureMonitorWorkspace
  properties: {
    targetId: serviceGroupId
  }
}

resource logAnalyticsWorkspaceMembership 'Microsoft.Relationships/serviceGroupMember@2023-09-01-preview' = {
  name: 'sgm-${uniqueString(serviceGroupId, logAnalyticsWorkspace.id)}'
  scope: logAnalyticsWorkspace
  properties: {
    targetId: serviceGroupId
  }
}

resource applicationInsightsMembership 'Microsoft.Relationships/serviceGroupMember@2023-09-01-preview' = {
  name: 'sgm-${uniqueString(serviceGroupId, applicationInsights.id)}'
  scope: applicationInsights
  properties: {
    targetId: serviceGroupId
  }
}

resource redisMembership 'Microsoft.Relationships/serviceGroupMember@2023-09-01-preview' = {
  name: 'sgm-${uniqueString(serviceGroupId, redisEnterprise.id)}'
  scope: redisEnterprise
  properties: {
    targetId: serviceGroupId
  }
}

resource containerRegistryMembership 'Microsoft.Relationships/serviceGroupMember@2023-09-01-preview' = {
  name: 'sgm-${uniqueString(serviceGroupId, containerRegistry.id)}'
  scope: containerRegistry
  properties: {
    targetId: serviceGroupId
  }
}
