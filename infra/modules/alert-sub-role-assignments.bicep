targetScope = 'subscription'

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

// Subscription-scoped Reader role for AKS Node OS auto-upgrade alert
// Required for ARG queries per official documentation
resource aksAlertSubReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, 'aks-nodeos-autoupgrade-alert', 'Reader', aksAlertPrincipalId)
  properties: {
    principalId: aksAlertPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: readerRoleDefinitionId
  }
}

// Subscription-scoped Reader role for Fleet pending approval alert
// Required for ARG queries per official documentation
resource fleetAlertSubReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableFleet && !empty(fleetAlertPrincipalId)) {
  name: guid(subscription().id, 'fleet-pending-approval-alert', 'Reader', fleetAlertPrincipalId)
  properties: {
    principalId: fleetAlertPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: readerRoleDefinitionId
  }
}
