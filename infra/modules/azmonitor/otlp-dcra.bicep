@description('Resource ID of the AKS cluster')
param aksClusterResourceId string

@description('Resource ID of the OTLP managed Data Collection Rule (from App Insights)')
param dataCollectionRuleId string

// Extract cluster name from resource ID
var aksClusterName = split(aksClusterResourceId, '/')[8]
var dataCollectionRuleAssociationName = 'OtlpAppInsightsExtension'

#disable-next-line BCP081
resource existingAksCluster 'Microsoft.ContainerService/managedClusters@2025-08-02-preview' existing = {
  name: aksClusterName
}

// Associate the OTLP managed DCR (auto-created by App Insights) with the AKS cluster.
// Without this association, ama-logs never loads the OTLP DCR config and port 4319 won't listen.
resource dataCollectionRuleAssociation 'Microsoft.Insights/dataCollectionRuleAssociations@2024-03-11' = {
  name: dataCollectionRuleAssociationName
  scope: existingAksCluster
  properties: {
    description: 'OTLP App Insights DCR association for AKS cluster. Required for ama-logs to accept OTLP telemetry.'
    dataCollectionRuleId: dataCollectionRuleId
  }
}

output dataCollectionRuleAssociationName string = dataCollectionRuleAssociation.name
