package com.example.paybox.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.example.paybox.ui.theme.PayBoxAvailableBackground
import com.example.paybox.ui.theme.PayBoxAvailableText
import com.example.paybox.ui.theme.PayBoxDisabledCard
import com.example.paybox.ui.theme.PayBoxDisabledCardText
import com.example.paybox.ui.theme.PayBoxGreen
import com.example.paybox.ui.theme.PayBoxSupportingText
import com.example.paybox.ui.theme.PayBoxTheme
import com.example.paybox.ui.theme.PayBoxUnavailableBackground
import com.example.paybox.ui.theme.PayBoxUnavailableText

@Composable
fun LockerList(
    modifier: Modifier = Modifier,
    onLockerClick: (Int) -> Unit = {}
) {
    val unavailableLockers = listOf(3, 5, 6, 9)

    LazyColumn(modifier = modifier) {
        item {
            Column(
                modifier = Modifier.padding(
                    horizontal = 16.dp,
                    vertical = 24.dp
                ),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Text(
                    text = "PayBox",
                    style = MaterialTheme.typography.headlineLarge,
                    color = Color.White
                )
                Text(
                    text = "Select a locker to rent",
                    style = MaterialTheme.typography.titleMedium,
                    color = Color.White
                )
                Text(
                    text = "Small €1 · Large €1.50 per day",
                    style = MaterialTheme.typography.bodyMedium,
                    color = PayBoxSupportingText
                )
            }
        }

        items(9) { index ->
            val lockerNumber = index + 1
            val isAvailable = lockerNumber !in unavailableLockers

            LockerCard(
                lockerNumber = lockerNumber,
                isAvailable = isAvailable,
                onClick = { onLockerClick(lockerNumber) }
            )
        }
    }
}

@Preview(showBackground = true)
@Composable
fun LockerListPreview() {
    PayBoxTheme {
        LockerList()
    }
}

@Composable
private fun LockerCard(
    lockerNumber: Int,
    isAvailable: Boolean,
    onClick: () -> Unit
) {
    Card(
        onClick = onClick,
        enabled = isAvailable,
        modifier = Modifier
            .fillMaxWidth()
            .padding(8.dp),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(
            containerColor = Color.White,
            contentColor = PayBoxGreen,
            disabledContainerColor = PayBoxDisabledCard,
            disabledContentColor = PayBoxDisabledCardText
        )
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(20.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Text(
                    text = "Locker $lockerNumber",
                    style = MaterialTheme.typography.titleLarge
                )
                Text(
                    text = if (lockerNumber <= 6) "Size: Small" else "Size: Large",
                    style = MaterialTheme.typography.bodyMedium
                )
            }
            Column(
                horizontalAlignment = Alignment.End,
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Surface(
                    color = if (isAvailable) {
                        PayBoxAvailableBackground
                    } else {
                        PayBoxUnavailableBackground
                    },
                    contentColor = if (isAvailable) {
                        PayBoxAvailableText
                    } else {
                        PayBoxUnavailableText
                    },
                    shape = RoundedCornerShape(50)
                ) {
                    Text(
                        text = if (isAvailable) "Available" else "Unavailable",
                        style = MaterialTheme.typography.labelLarge,
                        modifier = Modifier.padding(
                            horizontal = 12.dp,
                            vertical = 6.dp
                        )
                    )
                }
                Text(
                    text = if (lockerNumber <= 6) "€1 per day" else "€1.50 per day",
                    style = MaterialTheme.typography.bodyMedium
                )
            }
        }
    }
}
