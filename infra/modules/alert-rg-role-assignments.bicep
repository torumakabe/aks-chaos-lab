@description('Principal ID for AKS Node OS auto-upgrade alert rule')
param aksAlertPrincipalId string

@description('Principal ID for Fleet pending approval alert rule')
param fleetAlertPrincipalId string = ''

@description('Enable Fleet alert role assignment')
param enableFleet bool = false

// Reader role definition
var readerRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'acdd72a7-3385-48ef-bd42-f606fba81ae7'
)

// Assign Reader role to AKS alert rule's managed identity for ARG queries (RG scope)
resource aksAlertRgReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, resourceGroup().id, 'aks-nodeos-autoupgrade', 'Reader', aksAlertPrincipalId)
  scope: resourceGroup()
  properties: {
    principalId: aksAlertPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: readerRoleDefinitionId
  }
}

// Assign Reader role to Fleet alert rule's managed identity for ARG queries (RG scope)
resource fleetAlertRgReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableFleet && !empty(fleetAlertPrincipalId)) {
  name: guid(subscription().id, resourceGroup().id, 'fleet-approval-pending', 'Reader', fleetAlertPrincipalId)
  scope: resourceGroup()
  properties: {
    principalId: fleetAlertPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: readerRoleDefinitionId
  }
}
