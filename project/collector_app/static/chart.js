document.addEventListener("DOMContentLoaded", function () {
    var ctx = document.getElementById("wasteChart").getContext("2d");

    var wasteChart = new Chart(ctx, {
        type: "bar",
        data: {
            labels: ["Food Waste", "Plastic Waste", "Paper Waste"],
            datasets: [{
                label: "Waste Collected (kg)",
                data: [0, 0, 0],  // Initially empty
                backgroundColor: ["green", "blue", "orange"],
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        stepSize: 5  // Ensure bars are spaced properly
                    }
                }
            },
            plugins: {
                tooltip: {
                    enabled: true
                }
            }
        }
    });

    // Fetch statistics from the Flask server
    window.fetchStatistics = function () {
        var dateRange = document.getElementById("date_range").value;

        if (!dateRange) {
            alert("Please select a date range.");
            return;
        }

        var dates = dateRange.split(" to ");
        var startDate = dates[0];
        var endDate = dates[1] || startDate;  // If only one date is selected, use the same for start and end

        fetch("/get_statistics", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ start_date: startDate, end_date: endDate })
        })
        .then(response => response.json())
        .then(data => {
            console.log("API Response:", data);

            document.getElementById("food_waste").textContent = data.food_waste + " kg";
            document.getElementById("plastic_waste").textContent = data.plastic_waste + " kg";
            document.getElementById("paper_waste").textContent = data.paper_waste + " kg";

            // Ensure values are converted to numbers
            let food = Number(data.food_waste);
            let plastic = Number(data.plastic_waste);
            let paper = Number(data.paper_waste);

            // Update chart data
            wasteChart.data.datasets[0].data = [food, plastic, paper];

            // ✅ Add value labels above bars
            wasteChart.options.plugins = {
                datalabels: {
                    anchor: 'end',
                    align: 'top',
                    color: 'black',
                    font: {
                        weight: 'bold',
                        size: 14
                    },
                    formatter: function (value) {
                        return value + " kg";
                    }
                }
            };

            wasteChart.update();
        })
        .catch(error => console.error("Error fetching statistics:", error));
    };
});
