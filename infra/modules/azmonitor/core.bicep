@description('Deployment location')
param location string
@description('Tags object')
param tags object
@description('Log Analytics name')
param logAnalyticsName string
@description('App Insights name')
param applicationInsightsName string
@description('Azure Monitor Workspace name for app OTLP telemetry (separate from Prometheus AMW)')
param appAzureMonitorWorkspaceName string

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2025-07-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// Azure Monitor Workspace for app OTLP telemetry
// Must be separate from Prometheus AMW per OTLP App Insights requirements
resource appAzureMonitorWorkspace 'Microsoft.Monitor/accounts@2023-04-03' = {
  name: appAzureMonitorWorkspaceName
  location: location
  tags: tags
}

// App Insights with OTLP ingestion enabled via AKS Auto-Configuration (ADR-006)
// Preview API required for AzureMonitorWorkspaceIngestionMode property
#disable-next-line BCP081
resource applicationInsights 'Microsoft.Insights/components@2025-01-23-preview' = {
  name: applicationInsightsName
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalyticsWorkspace.id
    IngestionMode: 'LogAnalytics'
    AzureMonitorWorkspaceIngestionMode: 'OptedIn'
    AzureMonitorWorkspaceResourceId: appAzureMonitorWorkspace.id
  }
}

output logAnalyticsId string = logAnalyticsWorkspace.id
output logAnalyticsNameOut string = logAnalyticsWorkspace.name
output applicationInsightsId string = applicationInsights.id
output applicationInsightsNameOut string = applicationInsights.name
output applicationInsightsConnectionString string = applicationInsights.properties.ConnectionString
#disable-next-line BCP081
output applicationInsightsDataCollectionRuleId string = applicationInsights.properties.DataCollectionRuleResourceId
