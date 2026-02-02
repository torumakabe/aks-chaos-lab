@description('Deployment location')
param location string
@description('AKS name')
param aksName string
@description('Tags object')
param tags object = {}
@description('Kubernetes version (x.y or x.y.z). Only used in Base mode; Automatic mode automatically selects and manages stable versions.')
param kubernetesVersion string = '1.33'
@description('Node resource group name for AKS managed resources')
param nodeResourceGroupName string
@description('Node VM size')
param nodeVmSize string
@description('AKS subnet id')
param aksSubnetId string
@description('AKS API Server subnet id')
param aksApiSubnetId string = ''
@description('Log Analytics workspace resource ID for Container Insights')
param logAnalyticsWorkspaceId string

@description('Action Group resource ID for alerts (optional, leave empty for lab use)')
param actionGroupId string = ''

@description('AKS SKU mode - "Base" for traditional AKS with Cluster Autoscaler; "Automatic" for automated operations with Node Auto Provisioning. Default is "Base"')
@allowed([
  'Base'
  'Automatic'
])
param skuName string = 'Base'

var resourceGroupSuffix = uniqueString(resourceGroup().id)

// KQL queries for auto-upgrade alerts
var aksNodeOsAutoUpgradeKqlTemplate = sys.loadTextContent('templates/aks-nodeos-autoupgrade.kql')
var aksNodeOsAutoUpgradeKql = replace(aksNodeOsAutoUpgradeKqlTemplate, '{{AKS_ID}}', aksCluster.id)

// Common properties shared between Base and Automatic modes
// Both modes use the same identity, monitoring, and basic configurations
var aksCommonProperties = {
  nodeResourceGroup: nodeResourceGroupName
  dnsPrefix: 'dns${substring(resourceGroupSuffix, 0, 8)}'
  metricsProfile: {
    costAnalysis: { enabled: true }
  }
  workloadAutoScalerProfile: {
    verticalPodAutoscaler: {
      enabled: true
      addonAutoscaling: 'Enabled'
    }
  }
  // Enable Azure Monitor managed Prometheus (metrics) and container insights
  azureMonitorProfile: {
    containerInsights: {
      enabled: true
      logAnalyticsWorkspaceResourceId: logAnalyticsWorkspaceId
    }
    metrics: {
      enabled: true
      kubeStateMetrics: {
        metricAnnotationsAllowList: ''
        metricLabelsAllowlist: ''
      }
    }
    appMonitoring: {
      // Disabled by default to avoid double-instrumentation.
      // Note: The chaos-app in this repository uses manual instrumentation (OpenTelemetry SDK).
      // Flip to true only if you want AKS-managed auto-instrumentation instead.
      autoInstrumentation: {
        enabled: false
      }
      openTelemetryLogs: {
        enabled: true
      }
      openTelemetryMetrics: {
        enabled: true
      }
    }
  }
}

// Base mode specific properties
// Provides full control over Kubernetes version, node pools, and networking
var aksBaseSpecificProperties = {
  // In Base mode, allow explicit Kubernetes version control
  kubernetesVersion: kubernetesVersion
  oidcIssuerProfile: { enabled: true }
  securityProfile: { workloadIdentity: { enabled: true } }
  // Enable Azure RBAC for Kubernetes authorization to match Automatic mode security
  aadProfile: {
    managed: true
    enableAzureRbac: true
  }
  // Disable local accounts to enforce Azure AD/Entra ID-only authentication
  // Note: In Automatic mode, local accounts are disabled by default
  disableLocalAccounts: true
  // Enable API Server VNet Integration for Base mode
  apiServerAccessProfile: {
    enableVnetIntegration: true
    subnetId: aksApiSubnetId
  }
  // Auto-upgrade settings - only NodeImage for node OS upgrades
  autoUpgradeProfile: {
    nodeOSUpgradeChannel: 'NodeImage'
  }
  autoScalerProfile: {
    'daemonset-eviction-for-occupied-nodes': true
    'max-node-provision-time': '15m'
    'scale-down-delay-after-add': '10m'
  }
  networkProfile: {
    // Azure CNI overlay with Cilium dataplane
    networkPlugin: 'azure'
    networkPluginMode: 'overlay'
    networkDataplane: 'cilium'
    networkPolicy: 'cilium'
    advancedNetworking: {
      enabled: true
      observability: {
        enabled: true
      }
      security: {
        enabled: true
        advancedNetworkPolicies: 'FQDN'
      }
    }
  }
  ingressProfile: {
    webAppRouting: {
      enabled: true
    }
  }
  agentPoolProfiles: [
    {
      name: 'default'
      vmSize: nodeVmSize
      mode: 'System'
      // Distribute nodes across availability zones (1/2/3 if region supports)
      availabilityZones: [
        '1'
        '2'
        '3'
      ]
      vnetSubnetID: aksSubnetId
      orchestratorVersion: kubernetesVersion
      enableAutoScaling: true
      minCount: 1
      maxCount: 3
    }
  ]
}

// Automatic mode specific properties
// Simplifies operations with automated version management and optimized node configuration
var aksAutomaticSpecificProperties = {
  apiServerAccessProfile: {
    enableVnetIntegration: true
    subnetId: aksApiSubnetId
  }
  networkProfile: {
    advancedNetworking: {
      enabled: true
      observability: {
        enabled: true
      }
      security: {
        enabled: true
        advancedNetworkPolicies: 'FQDN'
      }
    }
  }
  // Agent pools are automatically managed in Automatic mode
  // System pool is fixed at 3 nodes for high availability
  agentPoolProfiles: [
    {
      name: 'system'
      vnetSubnetID: aksSubnetId
      mode: 'System'
      count: 3
    }
  ]
}

// Compose final properties based on selected SKU mode
// Union merges common properties with mode-specific configurations
var aksBaseProperties = union(aksCommonProperties, aksBaseSpecificProperties)
var aksAutomaticProperties = union(aksCommonProperties, aksAutomaticSpecificProperties)

// User Assigned Managed Identity for AKS cluster
resource aksIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-${aksName}'
  location: location
  tags: tags
}

#disable-next-line BCP081
resource aksCluster 'Microsoft.ContainerService/managedClusters@2025-06-02-preview' = {
  name: aksName
  location: location
  tags: tags
  // Enable Uptime SLA by switching AKS SKU tier
  sku: {
    name: skuName
    tier: 'Standard'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${aksIdentity.id}': {}
    }
  }
  properties: skuName == 'Base' ? aksBaseProperties : aksAutomaticProperties
  dependsOn: [
    aksSubnetNetworkContributorRole
  ]
}

// Assign Network Contributor on the AKS subnet to the cluster's managed identity
// Parse subnet and vnet names from the provided subnet resource ID
var subnetIdParts = split(aksSubnetId, '/')
var subnetName = last(subnetIdParts)
var virtualNetworkName = subnetIdParts[8]

@description('Existing VNet (parent of the AKS subnet)')
resource existingVirtualNetwork 'Microsoft.Network/virtualNetworks@2024-07-01' existing = {
  name: virtualNetworkName
}

@description('Existing AKS subnet (role assignment scope)')
resource existingAksSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-07-01' existing = {
  name: subnetName
  parent: existingVirtualNetwork
}

// Network Contributor role definition ID
var networkContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4d97b98b-1d4f-4787-a291-c67834d212e7'
)

resource aksSubnetNetworkContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(existingAksSubnet.id, 'NetworkContributor', aksIdentity.name)
  scope: existingAksSubnet
  properties: {
    roleDefinitionId: networkContributorRoleId
    principalId: aksIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Assign Network Contributor on the resource group for managing public IPs
resource aksResourceGroupNetworkContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, 'NetworkContributor', aksIdentity.name)
  properties: {
    roleDefinitionId: networkContributorRoleId
    principalId: aksIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Output AKS cluster identity principal ID for additional role assignments
output aksIdentityPrincipalId string = aksIdentity.properties.principalId

// AKS Managed Node OS Upgrade Schedule (weekly on Wednesday)
#disable-next-line BCP081
resource aksMaintenanceNodeConf 'Microsoft.ContainerService/managedClusters/maintenanceConfigurations@2025-06-02-preview' = {
  parent: aksCluster
  name: 'aksManagedNodeOSUpgradeSchedule'
  properties: {
    maintenanceWindow: {
      durationHours: 4
      schedule: {
        weekly: {
          dayOfWeek: 'Wednesday'
          intervalWeeks: 1
        }
      }
      startDate: '2025-04-01'
      startTime: '00:00'
      utcOffset: '+09:00'
    }
  }
}

// Node OS auto-upgrade alert (native resource)
resource aksNodeOSAutoUpgradeAlertRule 'Microsoft.Insights/scheduledQueryRules@2025-01-01-preview' = {
  name: 'aks-nodeos-autoupgrade'
  location: location
  tags: tags
  kind: 'LogAlert'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: 'aks-nodeos-autoupgrade'
    description: 'AKS Node OS auto-upgrade detected via ARG events'
    enabled: true
    scopes: [aksCluster.id]
    evaluationFrequency: 'PT30M'
    windowSize: 'PT60M'
    severity: 3
    autoMitigate: true
    criteria: {
      allOf: [
        {
          query: aksNodeOsAutoUpgradeKql
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          metricMeasureColumn: ''
          dimensions: [
            {
              name: 'status'
              operator: 'Include'
              values: ['*']
            }
          ]
        }
      ]
    }
    actions: {
      actionGroups: actionGroupId != '' ? [actionGroupId] : []
    }
  }
}

// Assign Reader role to the alert rule's managed identity so it can query ARG
resource aksNodeOSAutoUpgradeAlertRuleRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(
    subscription().id,
    resourceGroup().id,
    aksNodeOSAutoUpgradeAlertRule.name,
    'acdd72a7-3385-48ef-bd42-f606fba81ae7' // Reader
  )
  scope: resourceGroup()
  properties: {
    principalId: aksNodeOSAutoUpgradeAlertRule.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'acdd72a7-3385-48ef-bd42-f606fba81ae7'
    )
  }
}

output aksId string = aksCluster.id
output aksNameOut string = aksCluster.name
output kubeletObjectId string = aksCluster.properties.identityProfile.kubeletidentity.objectId
output oidcIssuerUrl string = aksCluster.properties.oidcIssuerProfile.issuerURL
