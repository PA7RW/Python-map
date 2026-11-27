
# dit is de tweede oefening uit Intro to Phyton
print (3+2) # dit is een simpele optelling
print (5-3) # dit is een simpele aftrekking
print (2*4) # dit is een simpele vermenigvuldiging
print (10/2) # dit is een simpele deling uitkomst in float
print (35%3) # dit is een simpele modulo, het restgetal van de deling
print (2**4) # dit is een simpele machtsverheffing
print ("3+2") # dit is een simpele optelling met strings
print (5-3,2) # dit is een simpele aftrekking en een strings

# er zijn integers en foats, integers zijn gehele getallen en floats zijn kommagetallen. beide worden verschillend behandeld door phyton. 
# integers worden gebruikt voor hele getallen en floats voor kommagetallen.

# f"tekst hier {code hier}"
print(f"3 + 2 = {3 + 2}") # dit levert de bewerking incl uitkomst op op je monitor en wordt een f-string genoemd. de f staat voor format. de uitkomst van de bewerking wordt in de string geplaatst.
print(f"5 - 1 = {5 - 1}")
print(f"3 * 4 = {3 * 4}")
print(f"10 / 5 = {10 / 5}")
print(f"35 % 3 = {35 % 3}")
print(f"2 ** 4 = {2 ** 4}")

# iteration wordt gebruikt om iets automatisch over en over te doen. een voorbeeld is een for loop. een for loop wordt gebruikt om iets een aantal keer te herhalen. 
# in dit geval wordt de print functie 10 keer uitgevoerd.
for i in range(10):
    #statement that gets repeated
    print("hello")
for i in range(10):
    print(i)

for i in range(3):
    print (i)
    print("--")

