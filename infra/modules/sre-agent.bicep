@description('Azure SRE Agent name')
param agentName string

@description('Azure SRE Agent region')
param location string

@description('Tags object')
param tags object

@description('Log Analytics workspace resource ID for SRE Agent telemetry')
param logAnalyticsWorkspaceId string

@description('Application Insights resource ID for the chaos app connector')
param applicationInsightsResourceId string

@description('Application Insights application ID for the chaos app connector')
param applicationInsightsAppId string

@description('Principal ID receiving the SRE Agent Administrator role')
param deployerPrincipalId string

@description('Monthly active-flow limit in Azure Agent Units')
param monthlyAgentUnitLimit int

@description('Default model provider')
param modelProvider string

@description('Default model name')
param modelName string

@description('Configure Azure Monitor as the incident platform')
param enableAzureMonitorIncidentIntegration bool = false
@description('Dedicated delegated subnet in the Agent region')
param subnetId string

var identityName = '${agentName}-id'
var agentTelemetryName = 'appi-${agentName}'

resource agentIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: identityName
  location: location
  tags: tags
}

resource agentTelemetry 'Microsoft.Insights/components@2020-02-02' = {
  name: agentTelemetryName
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    Request_Source: 'SreAgent'
    WorkspaceResourceId: logAnalyticsWorkspaceId
  }
}

module userAssignedTargetRoleAssignments './sre-agent-role-assignments.bicep' = {
  name: 'sreAgentUserAssignedTargetRoles'
  params: {
    principalId: agentIdentity.properties.principalId
  }
}

module systemAssignedTargetRoleAssignments './sre-agent-role-assignments.bicep' = {
  name: 'sreAgentSystemAssignedTargetRoles'
  params: {
    principalId: sreAgent.identity.principalId
  }
}

#disable-next-line BCP081
resource sreAgent 'Microsoft.App/agents@2026-01-01' = {
  name: agentName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${agentIdentity.id}': {}
    }
  }
  properties: union(
    {
      vnetConfiguration: {
        subnetResourceId: subnetId
      }
      sandboxConfiguration: {
        egress: {
          mode: 'AzureVNet'
          vnetConfiguration: {
            usePrivateDnsResolution: true
          }
          allowHttpMcpServerNetworkAccess: true
          allowedCodeRepositories: ['Github', 'AzureDevOps', 'GithubEnterprise']
          allowedHosts: [
            '*.azmk8s.io'
            'management.azure.com'
            '*.applicationinsights.azure.com'
            'api.applicationinsights.io'
            'api.loganalytics.io'
            'api.loganalytics.azure.com'
            '*.prometheus.monitor.azure.com'
            'api.github.com'
            'graph.microsoft.com'
            'aka.ms'
            'learn.microsoft.com'
            'azcliprod.blob.core.windows.net'
            'azcliextensionsync.blob.core.windows.net'
            'azurecliextensionsync.blob.core.windows.net'
          ]
        }
      }
      knowledgeGraphConfiguration: {
        identity: agentIdentity.id
        managedResources: [
          resourceGroup().id
        ]
      }
      actionConfiguration: {
        accessLevel: 'Low'
        identity: agentIdentity.id
        mode: 'Review'
      }
      logConfiguration: {
        applicationInsightsConfiguration: {
          appId: agentTelemetry.properties.AppId
          connectionString: agentTelemetry.properties.ConnectionString
          applicationInsightsResourceId: agentTelemetry.id
        }
      }
      upgradeChannel: 'Stable'
      monthlyAgentUnitLimit: monthlyAgentUnitLimit
      defaultModel: {
        provider: modelProvider
        name: modelName
      }
    },
    enableAzureMonitorIncidentIntegration
      ? {
          incidentManagementConfiguration: {
            type: 'AzMonitor'
            connectionName: 'azmonitor'
          }
        }
      : {}
  )
}

var applicationInsightsConnector = {
  name: 'chaos-app-insights'
  properties: {
    dataConnectorType: 'AppInsights'
    dataSource: applicationInsightsResourceId
    extendedProperties: {
      armResourceId: applicationInsightsResourceId
      resource: {
        name: last(split(applicationInsightsResourceId, '/'))
      }
      appId: applicationInsightsAppId
    }
    identity: 'system'
  }
}

var logAnalyticsConnector = {
  name: 'chaos-app-log-analytics'
  properties: {
    dataConnectorType: 'LogAnalytics'
    dataSource: logAnalyticsWorkspaceId
    extendedProperties: {
      armResourceId: logAnalyticsWorkspaceId
      resource: {
        name: last(split(logAnalyticsWorkspaceId, '/'))
      }
    }
    identity: 'system'
  }
}

resource agentConnectors 'Microsoft.App/agents/connectors@2025-05-01-preview' = [
  for connector in [
    applicationInsightsConnector
    logAnalyticsConnector
  ]: {
    parent: sreAgent
    name: connector.name
    properties: connector.properties
    dependsOn: [
      userAssignedTargetRoleAssignments
      systemAssignedTargetRoleAssignments
    ]
  }
]

resource agentAdministratorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(sreAgent.id, deployerPrincipalId, 'e79298df-d852-4c6d-84f9-5d13249d1e55')
  scope: sreAgent
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'e79298df-d852-4c6d-84f9-5d13249d1e55'
    )
    principalId: deployerPrincipalId
  }
}

output agentId string = sreAgent.id
output agentEndpoint string = sreAgent.properties.agentEndpoint
output managedIdentityId string = agentIdentity.id
output userAssignedPrincipalId string = agentIdentity.properties.principalId
output systemAssignedPrincipalId string = sreAgent.identity.principalId
