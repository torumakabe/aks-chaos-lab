@description('Deployment location')
param location string

@description('Fleet resource name')
param fleetName string

@description('Tags applied to the fleet resources')
param tags object = {}

@description('AKS managed cluster resource ID to join to the fleet')
param aksClusterId string

@description('Fleet member name (use lowercase alphanumeric and hyphen)')
param fleetMemberName string = 'base-cluster'

@description('Update strategy resource name')
param updateStrategyName string = 'base-manual-approval'

@description('Stage name for the update strategy')
param updateStageName string = 'base-stage'

@description('Display name for the manual approval gate')
param approvalDisplayName string = 'Manual approval required'

@description('Action Group resource ID for alerts (optional, leave empty for lab use)')
param actionGroupId string = ''

@description('Auto-upgrade channel to use for Fleet-managed updates')
@allowed([
  'Stable'
  'Rapid'
  'NodeImage'
  'TargetKubernetesVersion'
])
param autoUpgradeChannel string = 'Stable'

var memberGroupName = fleetMemberName


resource fleet 'Microsoft.ContainerService/fleets@2025-04-01-preview' = {
  name: fleetName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
}

var pendingApprovalGateKqlTemplate = sys.loadTextContent('./templates/pending-approval-gate.kql')
var pendingApprovalGateKql = replace(pendingApprovalGateKqlTemplate, '{{FLEET_RESOURCE_ID}}', toLower(fleet.id))

resource fleetMember 'Microsoft.ContainerService/fleets/members@2025-04-01-preview' = {
  name: fleetMemberName
  parent: fleet
  properties: {
    clusterResourceId: aksClusterId
    group: memberGroupName
  }
}

resource fleetUpdateStrategy 'Microsoft.ContainerService/fleets/updateStrategies@2025-04-01-preview' = {
  name: updateStrategyName
  parent: fleet
  properties: {
    strategy: {
      stages: [
        {
          name: updateStageName
          beforeGates: [
            {
              type: 'Approval'
              displayName: approvalDisplayName
            }
          ]
          groups: [
            {
              name: memberGroupName
            }
          ]
        }
      ]
    }
  }
  dependsOn: [
    fleetMember
  ]
}

resource autoUpgradeProfile 'Microsoft.ContainerService/fleets/autoUpgradeProfiles@2025-04-01-preview' = {
  name: 'default-auto-upgrade'
  parent: fleet
  properties: {
    updateStrategyId: fleetUpdateStrategy.id
    channel: autoUpgradeChannel
    disabled: false
    nodeImageSelection: {
      type: 'Latest'
    }
  }
}

resource nodeImageAutoUpgradeProfile 'Microsoft.ContainerService/fleets/autoUpgradeProfiles@2025-04-01-preview' = {
  name: 'default-nodeimage-auto-upgrade'
  parent: fleet
  properties: {
    updateStrategyId: fleetUpdateStrategy.id
    channel: 'NodeImage'
    disabled: false
  }
}

resource fleetPendingApprovalAlert 'Microsoft.Insights/scheduledQueryRules@2025-01-01-preview' = {
  name: 'fleet-approval-pending'
  location: location
  tags: tags
  kind: 'LogAlert'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'fleet-approval-pending'
    description: 'Fleet manual approval gate is pending approval.'
    enabled: true
    scopes: [resourceGroup().id]
    evaluationFrequency: 'PT30M'
    windowSize: 'PT60M'
    severity: 3
    autoMitigate: true
    criteria: {
      allOf: [
        {
          query: pendingApprovalGateKql
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          metricMeasureColumn: ''
          dimensions: []
        }
      ]
    }
    actions: {
      actionGroups: actionGroupId != '' ? [actionGroupId] : []
    }
  }
  dependsOn: [
    autoUpgradeProfile
    nodeImageAutoUpgradeProfile
  ]
}

resource fleetPendingApprovalAlertReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(
    subscription().id,
    resourceGroup().id,
    fleetPendingApprovalAlert.name,
    'acdd72a7-3385-48ef-bd42-f606fba81ae7' // Reader
  )
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'acdd72a7-3385-48ef-bd42-f606fba81ae7'
    )
    principalId: fleetPendingApprovalAlert.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output fleetId string = fleet.id
output fleetMemberId string = fleetMember.id
output updateStrategyId string = fleetUpdateStrategy.id
output autoUpgradeProfileId string = autoUpgradeProfile.id
output nodeImageAutoUpgradeProfileId string = nodeImageAutoUpgradeProfile.id
